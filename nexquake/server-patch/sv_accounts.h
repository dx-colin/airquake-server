/*
sv_accounts.h -- private login/registration, persistent per-account stats,
map voting and admin command support for the AirQuake dedicated server.

Ported from the QuakeSpasm version of this file (see the airquake-server
repo's vendor/quakespasm/Quake/sv_accounts.c) onto NexQuake's patched
WinQuake-derived nqserver, whose browser client can't talk to QuakeSpasm at
the wire-protocol level. See sv_accounts.c for the design notes -- the only
changes from the QuakeSpasm version are string-helper substitutions
(q_strlcpy/q_snprintf don't exist in this older codebase) and
Sys_FloatTime instead of Sys_DoubleTime.
*/

#ifndef _SV_ACCOUNTS_H_
#define _SV_ACCOUNTS_H_

#define SV_ACCOUNT_NAME_LEN	32
#define SV_ACCOUNT_HASH_LEN	128
#define SV_ACCOUNT_ERR_LEN	128

// Defined in host.c, alongside fraglimit/timelimit.
extern cvar_t	sv_votable_maps;	// space-separated map list eligible for /vote
extern cvar_t	sv_vote_threshold;	// fraction of eligible players needed to pass a vote

// Overrides the directory accounts.dat lives in, instead of deriving it
// from com_gamedir. Must be called before SV_Accounts_Init() (i.e. before
// Host_Init()) to take effect -- see sys_linux.c's -accountsdir handling.
void SV_Accounts_SetDir (const char *dir);

// Loads accounts.dat (creating it if missing) from com_gamedir, or from the
// SV_Accounts_SetDir() override if one was set. Call once, after
// COM_InitFilesystem() has resolved com_gamedir (i.e. from SV_Init).
void SV_Accounts_Init (void);

// Frees in-memory account state. Called from Host_Shutdown.
void SV_Accounts_Shutdown (void);

// Registers a new account and logs the given client into it immediately.
// Returns false and fills errmsg on failure (bad username/password, or the
// username is already taken).
qboolean SV_Accounts_Register (struct client_s *cl, const char *username,
	const char *password, char *errmsg, size_t errmsg_size);

// Authenticates a client against an existing account. Returns false and
// fills errmsg on failure (unknown username or wrong password).
qboolean SV_Accounts_Login (struct client_s *cl, const char *username,
	const char *password, char *errmsg, size_t errmsg_size);

// Logs a client out. Persists accrued playtime first. Safe to call on a
// client that isn't logged in (no-op).
void SV_Accounts_Logout (struct client_s *cl);

// Called from SV_DropClient before a client's state is cleared, so accrued
// session playtime gets persisted even on disconnect (not just /logout).
void SV_Accounts_OnDisconnect (struct client_s *cl);

// One-shot admin bootstrap/reset, driven by `nqserver -createadmin <user> <pass>`.
// Creates the account if it doesn't exist, or updates the password + admin
// flag if it does -- idempotent, so it doubles as a password-reset path.
// Loads/saves accounts.dat itself; does not require SV_Accounts_Init to
// have been called first. Returns false on a validation failure.
qboolean SV_Accounts_CreateAdmin (const char *username, const char *password);

// Sets an existing account's admin flag (does not create the account).
// Also updates the live client_t if that account is currently connected
// and authenticated, so a demotion/promotion takes effect immediately
// rather than on next login. Returns false and fills errmsg if no such
// account exists.
qboolean SV_Accounts_SetRole (const char *username, qboolean is_admin,
	char *errmsg, size_t errmsg_size);

// Inspects text about to go out via SV_BroadcastPrintf (i.e. QuakeC's
// bprint, which is how AirQuake's compiled progs.dat announces kills/
// deaths) and, if it matches a kill/death message, attributes it to
// whichever connected clients are currently logged in under that in-game
// name. Unauthenticated players' kills/deaths are simply not tracked.
void SV_Accounts_ObserveBroadcast (const char *text);

// Map voting -- tallied in memory, reset each time a new server is spawned.
void SV_Accounts_ResetVotes (void);
void SV_Accounts_CastVote (struct client_s *cl, const char *mapname);

// Live management dashboard (quake-manage, a separate service reading/
// writing files in the same accounts directory -- see sv_accounts.c's
// "live management dashboard" section for why this is file-based).
// Throttled internally to roughly once every 2 seconds; cheap to call
// every frame.
void SV_Accounts_DumpLiveStatus (void);
// Runs (queues, via Cbuf_AddText) a command quake-manage dropped in
// admin_command.txt, if any, then deletes the file. Call once per frame
// from a point that isn't inside a client's packet read (see host.c's
// _Host_Frame) -- same reentrancy concern as the vote-passed changelevel.
void SV_Accounts_ProcessPendingCommand (void);

#endif	/* _SV_ACCOUNTS_H_ */
