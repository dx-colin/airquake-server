/*
sv_accounts.c -- private login/registration, persistent per-account stats,
map voting and admin command support for the AirQuake dedicated server.

Design summary (see the airquake-server repo's plan for the full writeup):

  - AirQuake ships only a compiled PROGS.DAT (no QuakeC source exists for
    it), so none of this can live in the mod -- it's all engine-level.
  - Chat ("say"/"say_team") broadcasts to every player and is logged in
    plaintext, so it's unusable for credentials. Instead, players type
    brand-new client commands ("register", "login", "logout", "vote",
    "admin") directly at their own local console. A Quake client already
    forwards any console command it doesn't recognise locally straight to
    the server as a clc_stringcmd -- that's how "say" already works today
    -- so nothing here is ever shown to any other player, and no client
    patch is needed.
  - Feedback uses SV_ClientPrintf (private, one client only) for
    login/register/admin-denied responses, and SV_BroadcastPrintf (public)
    for vote tallies and admin rule-change announcements -- the same two
    primitives the stock engine already uses for e.g. Host_Tell_f (private)
    vs Host_Say_f (public).
  - Accounts persist to accounts.dat in the game directory (the same
    already-mounted, already-writable volume qconsole.log lives on), NOT
    qconsole.log itself -- qconsole.log gets truncated by -condebug on
    every server restart, so it was never suitable for anything durable.
  - Passwords are hashed with glibc/libxcrypt's crypt_r() (SHA-512-crypt,
    "$6$..."), which self-encodes algorithm+salt+rounds into one string --
    no separate salt column needed, no hand-rolled crypto.
  - The dedicated server's game loop is single-threaded (confirmed: no
    pthread/SDL audio thread runs in -dedicated mode), so no locking is
    needed anywhere in this file.
  - Writes are synchronous (every mutation immediately rewrites
    accounts.dat) rather than throttled: kill/login/vote events are rare
    enough on a small friends server that this is unmeasurable in
    practice, and it sidesteps needing a SIGTERM handler for a clean-
    shutdown flush (the stock engine installs none, so a throttled/
    deferred flush would risk losing data on `docker stop`). Writes are
    atomic (write to accounts.dat.tmp, then rename()) so a crash mid-write
    never corrupts the file.
*/

#define _GNU_SOURCE
#include "quakedef.h"
#include "sv_accounts.h"

#include <crypt.h>
#include <regex.h>
#include <time.h>
#include <unistd.h>

#define SV_MAX_ACCOUNTS	128
#define SV_VOTE_MAX_CANDIDATES	16

typedef struct
{
	char		username[SV_ACCOUNT_NAME_LEN];
	char		hash[SV_ACCOUNT_HASH_LEN];
	qboolean	is_admin;
	unsigned long	kills;
	unsigned long	deaths;
	unsigned long	playtime_seconds;
	long		created_at;
	long		last_login;
} account_t;

static account_t	g_accounts[SV_MAX_ACCOUNTS];
static int		g_num_accounts = 0;
static qboolean		g_accounts_initialized = false;

// Overrides where accounts.dat lives, instead of deriving it from
// com_gamedir. Needed under process managers that run the server with a
// gamedir that isn't the real persistent one -- e.g. NexQuake's Nexus
// spawns servers inside an ephemeral temp directory that symlinks in the
// real common/server layers and deletes the whole thing on every restart
// (see nexus/internal/assets/serverfs.go: PrepareRuntimeBasedir). Writing
// accounts.dat there would silently lose every account on each restart.
// Set via -accountsdir <path> (see main_sdl.c); empty means "use
// com_gamedir", which is correct for a normal standalone deployment.
static char		g_accounts_dir[MAX_OSPATH] = "";

void SV_Accounts_SetDir (const char *dir)
{
	q_strlcpy (g_accounts_dir, dir, sizeof (g_accounts_dir));
}

static const char *_SV_Accounts_Dir (void)
{
	return g_accounts_dir[0] ? g_accounts_dir : com_gamedir;
}

typedef struct
{
	char	mapname[64];
	int	votes;
} vote_entry_t;

static vote_entry_t	g_vote_candidates[SV_VOTE_MAX_CANDIDATES];
static int		g_vote_num_candidates = 0;

// ── kill/death message patterns (ported from stats/parse_stats.py's proven
//    regexes -- same mod, same log text, kept in sync deliberately) ──────

static regex_t	re_active_kill;
static regex_t	re_passive_kill;
static regex_t	re_suicide[5];
static qboolean	re_compiled = false;

static void _SV_Accounts_CompileRegexes (void)
{
	if (re_compiled)
		return;

	regcomp (&re_active_kill, "^(.+) killed (.+)$", REG_EXTENDED);
	regcomp (&re_passive_kill,
		"^(.+) was (killed|gibbed|fragged|destroyed|shot down|blasted|"
		"telefragged|blown up|taken out|terminated) by (.+)$", REG_EXTENDED);
	regcomp (&re_suicide[0], "^(.+) (killed|blew) (himself|herself|themselves)$", REG_EXTENDED);
	regcomp (&re_suicide[1], "^(.+) (suicided|cratered|drowned|melted|crashed)$", REG_EXTENDED);
	regcomp (&re_suicide[2], "^(.+) (fell to|fell from|plummeted to) (his |her |their )?death$", REG_EXTENDED);
	regcomp (&re_suicide[3], "^(.+) was in the wrong place$", REG_EXTENDED);
	regcomp (&re_suicide[4], "^(.+) tried to leave the map$", REG_EXTENDED);

	re_compiled = true;
}

// ── small helpers ─────────────────────────────────────────────────────────

static void _SV_Accounts_CopyMatch (const char *src, regmatch_t m, char *out, size_t outsize)
{
	size_t	len;

	if (m.rm_so < 0)
	{
		out[0] = 0;
		return;
	}
	len = (size_t)(m.rm_eo - m.rm_so);
	if (len >= outsize)
		len = outsize - 1;
	memcpy (out, src + m.rm_so, len);
	out[len] = 0;
}

static qboolean _SV_Accounts_ValidUsername (const char *username)
{
	size_t	i, len;

	len = strlen (username);
	if (len == 0 || len >= SV_ACCOUNT_NAME_LEN)
		return false;
	for (i = 0; i < len; i++)
	{
		unsigned char c = (unsigned char)username[i];
		if (c == ':' || c < 0x20 || c == 0x7f || c == ' ')
			return false;
	}
	return true;
}

static qboolean _SV_Accounts_ValidPassword (const char *password)
{
	size_t	i, len;

	len = strlen (password);
	if (len < 4 || len >= 256)
		return false;
	for (i = 0; i < len; i++)
	{
		unsigned char c = (unsigned char)password[i];
		if (c == ':' || c == '\r' || c == '\n')
			return false;
	}
	return true;
}

static account_t *_SV_Accounts_Find (const char *username)
{
	int	i;

	for (i = 0; i < g_num_accounts; i++)
		if (q_strcasecmp (g_accounts[i].username, username) == 0)
			return &g_accounts[i];
	return NULL;
}

static void _SV_Accounts_GenerateSalt (char *out, size_t outsize)
{
	static const char charset[] =
		"./0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz";
	size_t		i;
	FILE		*rnd;
	unsigned char	buf[32];

	if (outsize > sizeof (buf))
		outsize = sizeof (buf);

	rnd = fopen ("/dev/urandom", "rb");
	if (rnd)
	{
		if (fread (buf, 1, outsize - 1, rnd) != outsize - 1)
		{
			// fall through to the srand()-based fallback below
			fclose (rnd);
			rnd = NULL;
		}
		else
		{
			fclose (rnd);
		}
	}
	if (!rnd)
	{
		static qboolean seeded = false;
		if (!seeded)
		{
			srand ((unsigned int)(time (NULL) ^ getpid ()));
			seeded = true;
		}
		for (i = 0; i < outsize - 1; i++)
			buf[i] = (unsigned char)rand ();
	}

	for (i = 0; i < outsize - 1; i++)
		out[i] = charset[buf[i] % (sizeof (charset) - 1)];
	out[outsize - 1] = 0;
}

static qboolean _SV_Accounts_HashPassword (const char *password, char *out, size_t outsize)
{
	char			salt[17];
	char			setting[24];
	struct crypt_data	data;
	char			*result;

	_SV_Accounts_GenerateSalt (salt, sizeof (salt));
	q_snprintf (setting, sizeof (setting), "$6$%s$", salt);

	memset (&data, 0, sizeof (data));
	result = crypt_r (password, setting, &data);
	if (!result)
		return false;

	q_strlcpy (out, result, outsize);
	return true;
}

static qboolean _SV_Accounts_VerifyPassword (const char *password, const char *hash)
{
	struct crypt_data	data;
	char			*result;

	memset (&data, 0, sizeof (data));
	result = crypt_r (password, hash, &data);
	if (!result)
		return false;
	return strcmp (result, hash) == 0;
}

// ── load / save ──────────────────────────────────────────────────────────

static void _SV_Accounts_Load (void)
{
	char	path[MAX_OSPATH];
	FILE	*f;
	char	line[1024];

	g_num_accounts = 0;
	q_snprintf (path, sizeof (path), "%s/accounts.dat", _SV_Accounts_Dir ());

	f = fopen (path, "r");
	if (!f)
		return;	// no accounts yet -- fine, file gets created on first save

	while (fgets (line, sizeof (line), f))
	{
		char		*fields[8];
		int		nfields = 0;
		char		*p = line;
		char		*nl;
		account_t	*a;

		nl = strchr (line, '\n');
		if (nl)
			*nl = 0;
		if (line[0] == 0)
			continue;

		while (nfields < 8)
		{
			fields[nfields++] = p;
			p = strchr (p, ':');
			if (!p)
				break;
			*p = 0;
			p++;
		}
		if (nfields != 8)
			continue;	// malformed line -- skip rather than abort loading everything

		if (g_num_accounts >= SV_MAX_ACCOUNTS)
			break;

		a = &g_accounts[g_num_accounts++];
		q_strlcpy (a->username, fields[0], sizeof (a->username));
		q_strlcpy (a->hash, fields[1], sizeof (a->hash));
		a->is_admin = atoi (fields[2]) != 0;
		a->kills = strtoul (fields[3], NULL, 10);
		a->deaths = strtoul (fields[4], NULL, 10);
		a->playtime_seconds = strtoul (fields[5], NULL, 10);
		a->created_at = atol (fields[6]);
		a->last_login = atol (fields[7]);
	}

	fclose (f);
}

static qboolean _SV_Accounts_Save (void)
{
	char	path[MAX_OSPATH];
	char	tmppath[MAX_OSPATH];
	FILE	*f;
	int	i;

	q_snprintf (path, sizeof (path), "%s/accounts.dat", _SV_Accounts_Dir ());
	q_snprintf (tmppath, sizeof (tmppath), "%s/accounts.dat.tmp", _SV_Accounts_Dir ());

	f = fopen (tmppath, "w");
	if (!f)
	{
		Con_Printf ("SV_Accounts: failed to open %s for writing\n", tmppath);
		return false;
	}

	for (i = 0; i < g_num_accounts; i++)
	{
		account_t *a = &g_accounts[i];
		fprintf (f, "%s:%s:%d:%lu:%lu:%lu:%ld:%ld\n",
			a->username, a->hash, a->is_admin ? 1 : 0,
			a->kills, a->deaths, a->playtime_seconds,
			a->created_at, a->last_login);
	}

	fflush (f);
	fclose (f);

	if (rename (tmppath, path) != 0)
	{
		Con_Printf ("SV_Accounts: failed to replace %s\n", path);
		return false;
	}
	return true;
}

// ── public API ───────────────────────────────────────────────────────────

void SV_Accounts_Init (void)
{
	if (g_accounts_initialized)
		return;
	_SV_Accounts_CompileRegexes ();
	_SV_Accounts_Load ();
	g_accounts_initialized = true;
	Con_Printf ("SV_Accounts: loaded %d account(s)\n", g_num_accounts);
}

void SV_Accounts_Shutdown (void)
{
	if (!g_accounts_initialized)
		return;
	_SV_Accounts_Save ();
}

qboolean SV_Accounts_Register (struct client_s *cl, const char *username,
	const char *password, char *errmsg, size_t errmsg_size)
{
	account_t	*a;

	if (!_SV_Accounts_ValidUsername (username))
	{
		q_strlcpy (errmsg, "invalid username (1-31 chars, no spaces/colons)", errmsg_size);
		return false;
	}
	if (!_SV_Accounts_ValidPassword (password))
	{
		q_strlcpy (errmsg, "invalid password (4+ chars, no colons/newlines)", errmsg_size);
		return false;
	}
	if (_SV_Accounts_Find (username))
	{
		q_strlcpy (errmsg, "that username is already taken", errmsg_size);
		return false;
	}
	if (g_num_accounts >= SV_MAX_ACCOUNTS)
	{
		q_strlcpy (errmsg, "account storage is full, ask the server admin", errmsg_size);
		return false;
	}

	a = &g_accounts[g_num_accounts++];
	memset (a, 0, sizeof (*a));
	q_strlcpy (a->username, username, sizeof (a->username));
	if (!_SV_Accounts_HashPassword (password, a->hash, sizeof (a->hash)))
	{
		g_num_accounts--;
		q_strlcpy (errmsg, "internal error hashing password", errmsg_size);
		return false;
	}
	a->created_at = a->last_login = (long)time (NULL);
	_SV_Accounts_Save ();

	q_strlcpy (cl->account_name, a->username, sizeof (cl->account_name));
	cl->authenticated = true;
	cl->is_admin = a->is_admin;
	cl->session_start = Sys_DoubleTime ();
	return true;
}

qboolean SV_Accounts_Login (struct client_s *cl, const char *username,
	const char *password, char *errmsg, size_t errmsg_size)
{
	account_t	*a;

	a = _SV_Accounts_Find (username);
	if (!a || !_SV_Accounts_VerifyPassword (password, a->hash))
	{
		q_strlcpy (errmsg, "wrong username or password", errmsg_size);
		return false;
	}

	a->last_login = (long)time (NULL);
	_SV_Accounts_Save ();

	q_strlcpy (cl->account_name, a->username, sizeof (cl->account_name));
	cl->authenticated = true;
	cl->is_admin = a->is_admin;
	cl->session_start = Sys_DoubleTime ();
	return true;
}

void SV_Accounts_OnDisconnect (struct client_s *cl)
{
	account_t	*a;

	if (!cl->authenticated)
		return;

	a = _SV_Accounts_Find (cl->account_name);
	if (a && cl->session_start > 0)
	{
		double elapsed = Sys_DoubleTime () - cl->session_start;
		if (elapsed > 0)
			a->playtime_seconds += (unsigned long)elapsed;
		_SV_Accounts_Save ();
	}

	cl->authenticated = false;
	cl->is_admin = false;
	cl->session_start = 0;
	cl->account_name[0] = 0;
}

void SV_Accounts_Logout (struct client_s *cl)
{
	SV_Accounts_OnDisconnect (cl);
}

qboolean SV_Accounts_CreateAdmin (const char *username, const char *password)
{
	account_t	*a;

	SV_Accounts_Init ();	// idempotent -- safe even if SV_Init already called it

	if (!_SV_Accounts_ValidUsername (username) || !_SV_Accounts_ValidPassword (password))
	{
		Con_Printf ("createadmin: invalid username or password\n");
		return false;
	}

	a = _SV_Accounts_Find (username);
	if (!a)
	{
		if (g_num_accounts >= SV_MAX_ACCOUNTS)
		{
			Con_Printf ("createadmin: account storage full\n");
			return false;
		}
		a = &g_accounts[g_num_accounts++];
		memset (a, 0, sizeof (*a));
		q_strlcpy (a->username, username, sizeof (a->username));
		a->created_at = (long)time (NULL);
	}

	if (!_SV_Accounts_HashPassword (password, a->hash, sizeof (a->hash)))
	{
		Con_Printf ("createadmin: failed to hash password\n");
		return false;
	}
	a->is_admin = true;
	_SV_Accounts_Save ();

	Con_Printf ("createadmin: account '%s' is now an admin\n", a->username);
	return true;
}

// ── kill/death attribution ──────────────────────────────────────────────

static client_t *_SV_Accounts_FindClientByName (const char *name)
{
	int	i;

	for (i = 0; i < svs.maxclients; i++)
	{
		if (svs.clients[i].active && strcmp (svs.clients[i].name, name) == 0)
			return &svs.clients[i];
	}
	return NULL;
}

static void _SV_Accounts_CreditKill (const char *killer_name, const char *victim_name)
{
	client_t	*kc, *vc;
	account_t	*ka, *va;
	qboolean	dirty = false;

	if (killer_name && killer_name[0] && strcmp (killer_name, victim_name) != 0)
	{
		kc = _SV_Accounts_FindClientByName (killer_name);
		if (kc && kc->authenticated)
		{
			ka = _SV_Accounts_Find (kc->account_name);
			if (ka)
			{
				ka->kills++;
				dirty = true;
			}
		}
	}

	vc = _SV_Accounts_FindClientByName (victim_name);
	if (vc && vc->authenticated)
	{
		va = _SV_Accounts_Find (vc->account_name);
		if (va)
		{
			va->deaths++;
			dirty = true;
		}
	}

	if (dirty)
		_SV_Accounts_Save ();
}

void SV_Accounts_ObserveBroadcast (const char *text)
{
	regmatch_t	m[4];
	char		a[128], b[128];
	char		line[512];
	size_t		len;
	int		i;

	if (!g_accounts_initialized)
		return;

	// bprint text arrives with a trailing newline; regexes are anchored
	// with $ so strip it first.
	q_strlcpy (line, text, sizeof (line));
	len = strlen (line);
	while (len > 0 && (line[len - 1] == '\n' || line[len - 1] == '\r'))
		line[--len] = 0;
	if (len == 0)
		return;

	// Suicides first -- "X killed himself" would otherwise also match the
	// active-kill pattern below with victim="himself".
	for (i = 0; i < 5; i++)
	{
		if (regexec (&re_suicide[i], line, 4, m, 0) == 0)
		{
			_SV_Accounts_CopyMatch (line, m[1], a, sizeof (a));
			_SV_Accounts_CreditKill (NULL, a);
			return;
		}
	}

	if (regexec (&re_active_kill, line, 4, m, 0) == 0)
	{
		_SV_Accounts_CopyMatch (line, m[1], a, sizeof (a));
		_SV_Accounts_CopyMatch (line, m[2], b, sizeof (b));
		_SV_Accounts_CreditKill (a, b);
		return;
	}

	if (regexec (&re_passive_kill, line, 4, m, 0) == 0)
	{
		_SV_Accounts_CopyMatch (line, m[1], a, sizeof (a));	// victim
		_SV_Accounts_CopyMatch (line, m[3], b, sizeof (b));	// killer
		_SV_Accounts_CreditKill (b, a);
		return;
	}
}

// ── map voting ───────────────────────────────────────────────────────────

static qboolean _SV_Accounts_MapIsVotable (const char *mapname)
{
	char		list[1024];
	char		*tok, *save;

	q_strlcpy (list, sv_votable_maps.string, sizeof (list));
	tok = strtok_r (list, " \t", &save);
	while (tok)
	{
		if (q_strcasecmp (tok, mapname) == 0)
			return true;
		tok = strtok_r (NULL, " \t", &save);
	}
	return false;
}

void SV_Accounts_ResetVotes (void)
{
	int	i;

	g_vote_num_candidates = 0;
	for (i = 0; i < svs.maxclients; i++)
		svs.clients[i].voted_this_round = false;
}

void SV_Accounts_CastVote (struct client_s *cl, const char *mapname)
{
	vote_entry_t	*ve = NULL;
	int		i, eligible;

	if (!_SV_Accounts_MapIsVotable (mapname))
	{
		SV_ClientPrintf ("'%s' isn't in the votable map list.\n", mapname);
		return;
	}
	if (cl->voted_this_round)
	{
		SV_ClientPrintf ("You've already voted this round.\n");
		return;
	}

	for (i = 0; i < g_vote_num_candidates; i++)
	{
		if (q_strcasecmp (g_vote_candidates[i].mapname, mapname) == 0)
		{
			ve = &g_vote_candidates[i];
			break;
		}
	}
	if (!ve)
	{
		if (g_vote_num_candidates >= SV_VOTE_MAX_CANDIDATES)
		{
			SV_ClientPrintf ("Too many different maps have votes already, sorry.\n");
			return;
		}
		ve = &g_vote_candidates[g_vote_num_candidates++];
		q_strlcpy (ve->mapname, mapname, sizeof (ve->mapname));
		ve->votes = 0;
	}

	ve->votes++;
	cl->voted_this_round = true;

	eligible = 0;
	for (i = 0; i < svs.maxclients; i++)
		if (svs.clients[i].active && svs.clients[i].spawned)
			eligible++;

	SV_BroadcastPrintf ("%s voted for %s (%d/%d)\n",
		cl->name, ve->mapname, ve->votes, eligible);

	if (eligible > 0 && (float)ve->votes / (float)eligible >= sv_vote_threshold.value)
	{
		char cmd[96];
		SV_BroadcastPrintf ("Vote passed! Changing map to %s...\n", ve->mapname);
		q_snprintf (cmd, sizeof (cmd), "changelevel %s\n", ve->mapname);
		Cbuf_AddText (cmd);
		Cbuf_Execute ();
		// SV_SpawnServer (triggered by the changelevel above) calls
		// SV_Accounts_ResetVotes itself, so no explicit reset here.
	}
}
