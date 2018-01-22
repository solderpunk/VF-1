# VF-1
Command line gopher client.  High speed, low drag.

## Invocation

Start VF-1:

```
vf1
```

Start VF-1 with your bookmarks:

```
vf1 --bookmarks
```

Start VF-1 with your favourite site:

```
vf1 phlogosphere.org
```

## Installing

Ideally, you would install VF-1 as follows:

```
pip3 install VF-1
```

This should install the command `vf1` in either `/usr/local/bin`,
`/usr/bin`, or `~/.local/bin`.

Alternatively, check out the source code and install it as follows:

```
pip3 install .
```

If you're a developer, you probably want an "editable" installation:

```
pip3 install --editable .
```

If you're a developer on a Windows machine and your pip is broken, you
can also install as follows:

```
python3 setup.py install
```

And finally, if you just can't install it, just run the `vf1.py`
file directly.

```
python3 vf1.py
```

## Quick, informal tutorial-style introduction

VF-1 is built around an interactive command prompt, and has a very "REPL"
feeling.  You only need a small number of commands to get around, and most of
them can be abbreviated to one or two chars, so with practice you can get
around very quickly.  You can safely unplug your mouse the entire time you
are using VF-1. :)

Well, let's start off by heading to SDF to check out some nice phlogs!  Use the
"go" command:

```
VF-1> go sdf.org/
```

(don't leave off the trailing slash - this won't be necessary in future
releases, but right now it is required for technical reasons I won't get into)

(if you are very lazy, you can type "g sdf.org/" instead, i.e. you can
abbreviate "go" to "g")

You should see a listing of the SDF Gopherspace.  The different menu items are
indicated by numbers in square brackets, and the SDF Member PHLOGOSPHERE is
option [1], so go ahead and type "1" and then enter:

```
VF-1> 1
```

You should see all the phlogs fly by, and unless you have a very large monitor
some have probably run off the top of the screen.  This will not be an uncommon
problem, and there are various ways to deal with it.  Obviously, you can scroll
up in your terminal like always, but VF-1 gives you other ways to deal with
this.  After you have visited a gopher menu (as opposed to a document), if you
just press Enter (i.e. execute an empty line), VF-1 will print the first 10
items in the menu by themselves (any ASCII art etc. in the original listing is
removed in this mode).  Each time you press enter you will see the next ten
items in the listing.  Page through a few times to get a feel for it.

If you just want to see which phlogs have been updated lately, that's probably
enough for you.  But suppose you are really curious about one phlog in
particular.  Say you want to know what Tomasino has been up to.  You could
search for his phlog specifically:

```
VF-1> search tom
```

(if you are very lazy, you can use "/" instead of "search", i.e. "/ tom")

This will show you the phlogs with "tom" in them (it's a simple case-insensitive
search).  Tomasino will probably be [1] or [2] (depends whether tomatobodhi has
updated more recently :).  So go ahead and type "1" and hit enter again to enter
Tomasino's gopherhole.  Then you can type "2" and enter to go to his phlog, and
then "1" and enter to read his most recent entry about Kindles.

Suppose now you want to go back to the main SDF phlog listing.  Let's check out
your history:

```
VF-1> history
```

(if you are very lazy, you can abbreviate "history" to "hist", and in fact if
you are fiendishly lazy you can just use "h")

You should this time see a menu of the few places you've been so far.  The
phlogosphere list will probably be [2], so type "2" and enter to go back there.
By now you are probably getting the hang of using numbers to get around.

For this next bit, let's focus on gunnarfrost's phlog, because he writes very
nice short entries which work well for this.  Once you're at the main phlog
listing, do a:

```
VF-1> search frost
```

To easily find gunnarfrost's phlog and then press [1] to type the first entry.

Short and sweet!  Now, suppose you want to read his next post.  You *could* use
the "back" command to go back to the menu listing and then press "2", and then
do "back" and "3", "back" and "4", etc.  But it's much easier to just type:

```
VF-1> next
```

(or, if you are lazy, just "n")

This will automatically take you to the next item in the most recently seen
gopher menu after the one you just viewed.  So you can just hit "n" and enter
again and again to flip through the pages of gunnar's phlog.  Each one is much
shorter than a full screen, so this works very nicely.

Lately gunnarfrost is a good phlogger and wraps his entries at 70 or 80 chars or
thereabouts.  But if you keep hitting "n" you'll get to early entries where the
lines just keep going until your terminal wraps them (sorry, gunnarfrost, I
don't mean to single you out here, plenty of other folk do this too!).  Once
you've found one of these, try running:

```
VF-1> fold
```

And VF-1 will wrap the lines at 80 chars for you (assuming you have the "fold"
command installed on whatever system you are using).  This isn't the only helper
command of this kind available.  Get back to the main SDF phlog listing (either
by running "back" a few times or using "hist" and a number to jump straight to
it) and go to my phlog.  Unlike gunnarfrost, I appear to be physiologically
incapable of writing phlog posts which are less than a few whole screens long.
Go to one of these posts (say my most recent "assorted replies and
acknowledgements"), and watch the lines fly by.  Now try:

```
VF-1> less
```

This will pipe my giant entry through "less", so you can move back and forth and
read it.  Just press "q" when you're done like usual to get your VF-1 prompt
back.

I have quite a few references at the end of that entry.  You might be tempted to
pick up your mouse, highlight those URLs, and use the "go" command to visit
them.  Put that rodent down!  The mouse, that is, not the gopher.  Instead, try
this command:

```
VF-1> links
```

VF-1 will then scan the most recently viewed post for URLs.  Well, actually, it
scans for words (i.e. things separated by spaces) which contain "://" and at
least one ".".  This might not catch all URLs and it might sometimes catch
things which are not URLs, but it works well enough for now.  You will see a
menu and now you can use numbers to follow any of those links without your
mouse!

If you want to know the URL of a document you are at so that you can refer to
it, just do:

```
VF-1> url
```

If you want to save the document, just do:

```
VF-1> save ~/some/random/path/somefilename.txt
```

Everything so far has been text-based.  Gopher items with itemtype 0 (text) are
fed to the "cat" command by default, or to "less" or "fold" if you request it.
But VF-1 can handle other itemtypes too.  Image files with an item type of "g"
or "I" will be opened using the "feh" image viewer (if installed).  HTML
content with an item type of "h" will be fed to "lynx --dump", and audio files
with an item type of "a" will be fed to "mpg123" (e.g. you can listen to jynx's
doom metal songs in this way).  Obviously if you do not have one of these
programs installed, it will not work.  In future I may provide some nice way to
customise which 3rd party programs are used for different item types.  For now,
if you want to use different programs, you will have to edit the code.  It's not
hard, just look for the _HANDLERS dictionary near the top of vf1.py and change
accordingly.

You probably need some bookmarks, right? Here's how to add the current
URL to your bookmarks. You can provide your own name, if you want.

```
VF-1> add
```

(or, if you are as lazy as usual, just "a")

If you want to reorganize your bookmarks, just open
`~/.vf1-bookmarks.txt` using a text editor and do it.

If you want to look at your bookmarks:

```
VF-1> bookmarks
```

(or, if you feel comfortably lazy, just "bm")

Sometimes you're looking at a menu and it's very long but you know you
want to look at few items, one after another. Assume you're looking at
`phlogosphere.org`, for example. How about adding the first four items
to a *tour* and then going on that tour?

```
VF-1> tour 1 2 3 4
VF-1> tour
```

Use the tour command without any arguments to go to the next stop.
This is basically your stack of items to go to. (And yes, you guessed
it. Use "t" if you're feeling lazy.)

But there's more. Let's say you're looking at something pretty
interesting, like the list of all the phlogs on `phlogosphere.org`.
How about marking this place with a letter, following some links, and
then returning to this location not using a bunch of "back" and "up"
commands but just that one letter?

```
VF-1> mark x
VF-1> ... do some stuff ...
VF-1> go x
```

(And yes, "m" for the lazy.)

To make a few implicit concepts explicit:

* VF-1 always has in it's mind exactly one "index", i.e. a list of places in
  Gopherspace with numbers attached to them.  By typing "1" and enter, "2" and
  enter, etc. you jump to that location in the active index.
* Whenever you visit a gopher menu, the contents of that menu become the active
  index, replacing whatever it used to be.
* When you do "search" or "history" or "links", the results of these commands
  overwrite your current index.  If you want to get your index back to being
  the most recently visited gopher menu, you can use the "ls" command.  Doing
  this means you lose your search results (your history doesn't go away,
  though).
* The "search" command runs on whatever the current index is.  This might not
  be the contents of a gopher menu.  You can search your history, and in fact
  you can even search the results of an earlier search to narrow things down!
* In general, VF-1 does not remember much.  It always has some idea of the most
  recently visited gopher menu (i.e. itemtype 1) and the most recently visited
  gopher document (i.e. any other itemtype).  "ls" always operates on the most
  recently visited gopher *menu*, even if you have visited some documents since
  then.  Commands like "fold", "less" and "save" operate on the most recently
  visited *document*, even if you have visited some menus since then.  Basically
  everything operates one the most recently seen thing of the appropriate type.
