ASP Resources Forum. (beta)

Thank you for downloading the ASP Forum.

This is going to be a very basic read me, but there will be a full guide with the final release.

Installation Instructions
=================

Unzip the forum.zip file in to a directory on your server. 

When the files are extracted, another zip (admin.zip)  will be shown. 

Create a new directory (/admin) off the directory where you installed the forum and unzip admin.zip here.  This directory should be password protected.

In the main forum directory there are 3 .inc files 

Config.inc
Profile.inc
Top.inc

These need to be placed in the root directory of the website.  Or if you place them elsewhere you will need to edit the <!—include………  in each page.

The ubbs.mdb file needs to be places in to a directory where write permissions have been set.  It’s also advisable to make sure this directory is not accessible by the web to stop people downloading the database.

Now all you need to get it running is to edit the config.inc file.

In here you need to set you connection string for the database.  This could either be your DSN or a DSN’less connection.   Examples of each are included.

Pword.asp, post_info.asp and post_page.asp needs to be edited.  This file sends an email to remind users of their password.  You would need to modify the email code of this page to match your email component.

If you don’t have an email component I would recommend the W3Jmail from www.dimac.net.  It’s good and free!

That should be it.  Any problems please use the Support Forum at http://www.asp-dev.ml.org/forum/default.asp

List of Files
========

config.inc -  Configuration file.  Final version will contain more information.
Default.asp - Displays all the available forums.   
Mail.asp - Displays the email page.  (final version will have a form to email the person)
Post.asp - This displays the form for posting messages.  It displays two slightly different forms depending on whether a topic or a reply to a topic is being posted.
Post_info.asp - This does the database update, from the info posted in post.asp
Profile.asp - This has a number of functions.  It can either display someone’s profile in a popup window, allow you to edit your profile.  It also does the DB update.
Profile.inc - This is the form used by profile.asp.  Again displays two slightly different forms depending on if you are editing or creating a profile.
Pword.asp - This allows a user to be reminded of their password.  This page displays the form, and sends an email.
Register.asp - This will register a user with the forum.
Search.asp - This is the search page.  It’s quite effective, but not very efficient!
Top.inc - This is the code for the top of each page.  Edit this to match your site.
Topic.inc - This displays the topics.
Ubbs.mdb - The Database for the forum.

Admin/

Add.asp - This can either add a new forum or category to the database.
Content.htm - Shows list of options.
Default.asp - The frame set.
Main.htm - The initial screen.
Top_frame.htm - The title page.
View.asp - Shows users.

Please not most of the admin section is incomplete at the moment. Though the Add forums, and Categories works fine.  So you can at least set your forum up!  The rest will be finished soon!

That’s about it!  If you have any problems, or if you find any bugs please use the support forum at - http://www.asp-dev.com/forum/default.asp

And if you have any suggestions for improving the forum, please let me know at the same forum.

Thanks.

John Penfold
http://www.asp-dev.com/
asp@asp-dev.com
