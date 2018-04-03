#!/usr/bin/env python3
# VF-1 Gopher client
# (C) 2018 Solderpunk <solderpunk@sdf.org>
# With contributions from:
#  - Alex Schroeder <alex@gnu.org>
#  - Joseph Lyman <tfurrows@sdf.org>

import argparse
import cmd
import collections
import fnmatch
import io
import mimetypes
import os.path
import random
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import traceback
import urllib.parse
import ssl

# Command abbreviations
_ABBREVS = {
    "a":    "add",
    "b":    "back",
    "bm":   "bookmarks",
    "book": "bookmarks",
    "f":    "fold",
    "fo":   "forward",
    "g":    "go",
    "h":    "history",
    "hist": "history",
    "l":    "less",
    "li":   "links",
    "m":    "mark",
    "n":    "next",
    "p":    "previous",
    "prev": "previous",
    "q":    "quit",
    "r":    "reload",
    "s":    "save",
    "se":   "search",
    "/":    "search",
    "t":    "tour",
    "u":    "up",
    "v":    "veronica",
}

# Programs to handle different item types
_ITEMTYPE_TO_MIME = {
    "1":    "text/plain",
    "0":    "text/plain",
    "h":    "text/html",
    "g":    "image/gif",
}

_MIME_HANDLERS = {
    "application/pdf":      "xpdf %s",
    "audio/mpeg":           "mpg123 %s",
    "audio/ogg":            "ogg123 %s",
    "image/*":              "feh %s",
    "text/html":            "lynx -dump -force_html %s",
    "text/plain":           "cat %s",
}

# Item type formatting stuff
_ITEMTYPE_TITLES = {
    "7":        " <INP>",
    "8":        " <TEL>",
    "9":        " <BIN>",
    "g":        " <IMG>",
    "h":        " <HTM>",
    "s":        " <SND>",
    "I":        " <IMG>",
    "T":        " <TEL>",
}

_ANSI_COLORS = {
    "red":      "\x1b[0;31m",
    "green":    "\x1b[0;32m",
    "yellow":   "\x1b[0;33m",
    "blue":     "\x1b[0;34m",
    "purple":   "\x1b[0;35m",
    "cyan":     "\x1b[0;36m",
    "white":    "\x1b[0;37m",
    "black":    "\x1b[0;30m",
}

_ITEMTYPE_COLORS = {
    "0":        _ANSI_COLORS["green"],    # Text File
    "1":        _ANSI_COLORS["blue"],     # Sub-menu
    "7":        _ANSI_COLORS["red"],      # Search / Input
    "8":        _ANSI_COLORS["purple"],   # Telnet
    "9":        _ANSI_COLORS["cyan"],     # Binary
    "g":        _ANSI_COLORS["blue"],     # Gif
    "h":        _ANSI_COLORS["green"],    # HTML
    "s":        _ANSI_COLORS["cyan"],     # Sound
    "I":        _ANSI_COLORS["cyan"],     # Gif
    "T":        _ANSI_COLORS["purple"],   # Telnet
}

# Lightweight representation of an item in Gopherspace
GopherItem = collections.namedtuple("GopherItem",
        ("host", "port", "path", "itemtype", "name", "tls"))

def url_to_gopheritem(url):
    u = urllib.parse.urlparse(url)
    # https://tools.ietf.org/html/rfc4266#section-2.1
    path = u.path
    if u.path and u.path[0] == '/' and len(u.path) > 1:
        itemtype = u.path[1]
        path = u.path[2:]
    else:
        # Use item type 1 for top-level selector
        itemtype = 1
    return GopherItem(u.hostname, u.port or 70, path,
                      str(itemtype), "<direct URL>",
                      True if u.scheme == "gophers" else False)

def gopheritem_to_url(gi):
    if gi and gi.host:
        return ("gopher%s://%s:%d/%s%s" % (
            "s" if gi.tls else "",
            gi.host, int(gi.port),
            gi.itemtype, gi.path))
    elif gi:
        return gi.path
    else:
        return ""

def gopheritem_from_line(line, tls):
    # Split on tabs.  Strip final element after splitting,
    # since if we split first we loose empty elements.
    parts = line.split("\t")
    parts[-1] = parts[-1].strip()
    # Discard Gopher+ noise
    if parts[-1] == "+":
        parts = parts[:-1]
    # Attempt to assign variables.  This may fail.
    # It's up to the caller to catch the Exception.
    name, path, server, port = parts
    itemtype = name[0]
    name = name[1:]
    return GopherItem(server, port, path, itemtype, name, tls)

def gopheritem_to_line(gi, name=""):
    # Prepend itemtype to name
    name = str(gi.itemtype) + (name or gi.name)
    return "\t".join((name, gi.path, gi.host or "", str(gi.port))) + "\n"

# Cheap and cheerful URL detector
def looks_like_url(word):
    return "." in word and word.startswith(("gopher://", "gophers://"))

class GopherClient(cmd.Cmd):

    def __init__(self, tls=False):
        cmd.Cmd.__init__(self)
        self.set_prompt(tls)
        self.tmp_filename = ""
        self.idx_filename = ""
        self.index = []
        self.index_index = -1
        self.history = []
        self.hist_index = 0
        self.page_index = 0
        self.lookup = self.index
        self.gi = None
        self.waypoints = []
        self.marks = {}
        self.mirrors = {}

        self.options = {
            "auto_page" : False,
            "auto_page_threshold" : 40,
            "color_menus" : False,
        }

    def set_prompt(self, tls):
        self.tls = tls
        if self.tls:
            self.prompt = "\x1b[38;5;196m" + "VF-1" + "\x1b[38;5;255m" + "> " + "\x1b[0m"
        else:
            self.prompt = "\x1b[38;5;202m" + "VF-1" + "\x1b[38;5;255m" + "> " + "\x1b[0m"

    def _go_to_gi(self, gi, update_hist=True):
        # Telnet is a completely separate thing
        if gi.itemtype in ("8", "T"):
            if gi.path:
                subprocess.call(shlex.split("telnet -l %s %s %s" % (gi.path, gi.host, gi.port)))
            else:
                subprocess.call(shlex.split("telnet %s %s" % (gi.host, gi.port)))
            if update_hist:
                self._update_history(gi)
            return
        elif gi.itemtype == "S":
            subprocess.call(shlex.split("ssh %s@%s -p %s" % (gi.path, gi.host, gi.port)))
            if update_hist:
                self._update_history(gi)
            return

        # From here on in, it's gopher only
        # Hit the network
        try:
            # Is this a local file?
            if not gi.host:
                f = open(gi.path, "rb")
            # Is this a search point?
            elif gi.itemtype == "7":
                query_str = input("Query term: ")
                f = send_query(gi.path, query_str, gi.host, gi.port or 70, self.tls)
            else:
                f = send_selector(gi.path, gi.host, gi.port or 70, self.tls)

        # Catch network exceptions, which may be recoverable if a redundant
        # mirror is specified
        except (socket.gaierror, ConnectionRefusedError,
                ConnectionResetError, TimeoutError, socket.timeout,
                ) as network_error:
            # Print an error message
            if isinstance(network_error, socket.gaierror):
                print("ERROR: DNS error!")
            elif isinstance(network_error, ConnectionRefusedError):
                print("ERROR: Connection refused!")
            elif isinstance(network_error, ConnectionResetError):
                print("ERROR: Connection reset!")
            elif isinstance(network_error, TimeoutError):
                print("ERROR: Connection timed out!")
                if self.tls:
                    print("Disable battloid mode using 'tls' to enter civilian territory.")
                else:
                    print("Switch to battloid mode using 'tls' to enable encryption.")
            elif isinstance(network_error, socket.timeout):
                print("ERROR: This is taking too long.")
                if not self.tls:
                    print("Switch to battloid mode using 'tls' to enable encryption.")
            # Try to fall back on a redundant mirror
            new_gi = self._get_mirror_gi(gi)
            if new_gi:
                print("Trying redundant mirror %s..." % gopheritem_to_url(new_gi))
                self._go_to_gi(new_gi)
            return

        # Catch non-network exceptions
        except OSError:
            print("ERROR: Operating system error... Recovery initiated...")
            print("Consider toggling battloid mode using 'tls' to adapt to the new situation.")
            return
        except ssl.SSLError as err:
            print("ERROR: " + err.reason)
            if err.reason == "UNKNOWN_PROTOCOL":
                print(gopheritem_to_url(gi) + " is probably not encrypted.")
                print("In battloid mode, encryption is mandatory.")
                print("Use 'tls' to toggle battloid mode.")
            return

        # Attempt to decode something that is supposed to be text
        if gi.itemtype in ("0", "1", "7", "h"):
            try:
                f = self._decode_text(f)
            except UnicodeError:
                print("ERROR: Unsupported text encoding!")
                return

        # Save the result in a temporary file
        ## Delete old file
        if self.tmp_filename:
            os.unlink(self.tmp_filename)
        ## Set file mode
        if gi.itemtype in ("0", "1", "7", "h"):
            mode = "w"
            encoding = "UTF-8"
        else:
            mode = "wb"
            encoding = None
        ## Write
        tmpf = tempfile.NamedTemporaryFile(mode, encoding=encoding, delete=False)
        tmpf.write(f.read())
        tmpf.close()
        self.tmp_filename = tmpf.name

        # Process that file handler depending upon itemtype
        if gi.itemtype in ("1", "7"):
            f.seek(0)
            self._handle_index(f)
        else:
            cmd_str = self.get_handler_cmd(gi)
            try:
                subprocess.call(shlex.split(cmd_str % tmpf.name))
            except FileNotFoundError:
                print("Handler program %s not found!" % shlex.split(cmd_str)[0])
                print("You can use the ! command to specify another handler program or pipeline.")

        # Update state
        self.gi = gi
        if update_hist:
            self._update_history(gi)

    def get_handler_cmd(self, gi):
        # First, get mimetype, either from itemtype or filename
        if gi.itemtype in _ITEMTYPE_TO_MIME:
            mimetype = _ITEMTYPE_TO_MIME[gi.itemtype]
        else:
            mimetype, encoding = mimetypes.guess_type(gi.path)
            if mimetype is None:
                # No idea what this is, try harder by looking at the
                # magic number using file(1)
                out = subprocess.check_output(
                    shlex.split("file --brief --mime-type %s" % self.tmp_filename))
                mimetype = out.decode("UTF-8").strip()
            # Don't permit file extensions to completely override the
            # vaguer imagetypes
            if gi.itemtype == "I" and not mimetype.startswith("image"):
                # The server declares this to be an image.
                # But it has a weird or missing file extension, so the
                # MIME type was guessed as something else.
                # We shall trust the server that it's an image.
                # Pretend it's a jpeg, because whatever handler the user has
                # set for jpegs probably has the best shot at handling this.
                mimetype = "image/jpeg"
            elif gi.itemtype == "s" and not mimetype.startswith("audio"):
                # As above, this is "weird audio".
                # Pretend it's an mp3?
                mimetype = "audio/mpeg"

        # Now look for a handler for this mimetype
        # Consider exact matches before wildcard matches
        exact_matches = []
        wildcard_matches = []
        for handled_mime, cmd_str in _MIME_HANDLERS.items():
            if "*" in handled_mime:
                wildcard_matches.append((handled_mime, cmd_str))
            else:
                exact_matches.append((handled_mime, cmd_str))
        for handled_mime, cmd_str in exact_matches + wildcard_matches:
            if fnmatch.fnmatch(mimetype, handled_mime):
                break
        else:
            # Use "strings" as a last resort.
            cmd_str = "strings %s"
        return cmd_str

    def _decode_text(self, f):
        # Attempt to decode some bytes into Unicode according to the three
        # most commonly used encodings on the web.  These 3 cover 95% of
        # web content, so hopefully will work well in Goperhspace.  If none
        # of these encodings work, raise UnicodeError
        encodings = ["UTF-8", "iso-8859-1", "cp-1251"]
        raw_bytes = f.read()
        while True:
            enc = encodings.pop(0)
            try:
                text = raw_bytes.decode(enc)
                break
            except UnicodeError as e:
                if not encodings:
                    # No encodings left to try, so reraise UnicodeError and
                    # let the caller handle it
                    raise e
        new_f = io.StringIO()
        new_f.write(text)
        new_f.seek(0)
        return new_f

    def _handle_index(self, f):
        self.index = []
        if self.idx_filename:
            os.unlink(self.idx_filename)
        tmpf = tempfile.NamedTemporaryFile("w", encoding="UTF-8", delete=False)
        self.idx_filename = tmpf.name
        menu_lines = 0
        self.page_index = 0
        for line in f:
            if line.startswith("3"):
                print("Error message from server:")
                print(line[1:].split("\t")[0])
                tmpf.close()
                os.unlink(self.idx_filename)
                self.idx_filename = ""
                return
            elif line.startswith("i"):
                tmpf.write(line[1:].split("\t")[0] + "\n")
                menu_lines += 1
            else:
                try:
                    gi = gopheritem_from_line(line, self.tls)
                except:
                    # Silently ignore things which are not errors, information
                    # lines or things which look like valid menu items
                    continue
                if gi.itemtype == "+":
                    self._register_redundant_server(gi)
                    continue
                self.index.append(gi)
                tmpf.write(self._format_gopheritem(len(self.index), gi) + "\n")
                menu_lines += 1
                if self.options["auto_page"] and menu_lines == self.options["auto_page_threshold"]:
                    self.page_index = len(self.index)
        tmpf.close()

        self.lookup = self.index
        self.index_index = -1

        if self.options["auto_page"]:
            subprocess.call(shlex.split("head -n %d %s" %
                (self.options["auto_page_threshold"],
                self.idx_filename)))
            if menu_lines > self.options["auto_page_threshold"]:
                print("""...
(Long menu truncated.
 Use 'cat' or 'less' to view whole menu, including informational messages.
 Use 'ls', 'search' or blank line pagination to view only menu entries.)""")
        else:
            cmd_str = _MIME_HANDLERS["text/plain"]
            subprocess.call(shlex.split(cmd_str % self.idx_filename))

    def _register_redundant_server(self, gi):
        # This mirrors the last non-mirror item
        target = self.index[-1]
        target = (target.host, target.port, target.path)
        if target not in self.mirrors:
            self.mirrors[target] = []
        self.mirrors[target].append((gi.host, gi.port, gi.path))

    def _get_mirror_gi(self, gi):
        # Search for a redundant mirror that matches this GI
        for (host, port, path_prefix), mirrors in self.mirrors.items():
            if (host == gi.host and port == gi.port and
                gi.path.startswith(path_prefix)):
                break
        else:
        # If there are no mirrors, we're done
            return None
        # Pick a mirror at random and build a new GI for it
        mirror_host, mirror_port, mirror_path = random.sample(mirrors, 1)[0]
        new_gi = GopherItem(mirror_host, mirror_port,
                mirror_path + "/" + gi.path[len(path_prefix):],
                gi.itemtype, gi.name, gi.tls)
        return new_gi

    def _format_gopheritem(self, index, gi, name=True, url=False):
        line = "[%d] " % index
        # Add item name, with itemtype indicator for non-text items
        if name:
            line += gi.name
            if gi.itemtype in _ITEMTYPE_TITLES:
                line += _ITEMTYPE_TITLES[gi.itemtype]
            elif gi.itemtype == "1":
                line += "/"
        # Add URL if requested
        if url:
            line += " (%s)" % gopheritem_to_url(gi)
        # Colourise
        if self.options["color_menus"] and gi.itemtype in _ITEMTYPE_COLORS:
            line = _ITEMTYPE_COLORS[gi.itemtype] + line + "\x1b[0m"
        return line

    def show_lookup(self, offset=0, end=None, name=True, url=False, reverse=False):
        if reverse:
            iterator = enumerate(self.lookup[end:offset:-1])
        else:
            iterator = enumerate(self.lookup[offset:end])
        for n, gi in iterator:
            print(self._format_gopheritem(n+offset+1, gi, name, url))

    def _update_history(self, gi):
        # Don't duplicate
        if self.history and self.history[self.hist_index] == gi:
            return
        self.history = self.history[0:self.hist_index+1]
        self.history.append(gi)
        self.hist_index = len(self.history) - 1

    def _get_active_tmpfile(self):
        return self.idx_filename if self.gi.itemtype == "1" else self.tmp_filename

    # Cmd implementation follows
    def default(self, line):
        if line.strip() == "EOF":
            return self.onecmd("quit")
        elif line.strip() == "..":
            return self.do_up()
        elif line.startswith("/"):
            return self.do_search(line[1:])

        # Expand abbreviated commands
        first_word = line.split()[0].strip()
        if first_word in _ABBREVS:
            full_cmd = _ABBREVS[first_word]
            expanded = line.replace(first_word, full_cmd, 1)
            return self.onecmd(expanded)

        # Try to parse numerical index for lookup table
        try:
            n = int(line.strip())
        except ValueError:
            print("What?")
            return

        try:
            gi = self.lookup[n-1]
        except IndexError:
            print ("Index too high!")
            return

        self.index_index = n
        self._go_to_gi(gi)

    ### Settings
    def do_set(self, line):
        """View or set various options."""
        if not line.strip():
            # Show all current settings
            for option in sorted(self.options.keys()):
                print("%s   %s" % (option, self.options[option]))
        elif len(line.split()) == 1:
            option = line.strip()
            if option in self.options:
                print("%s   %s" % (option, self.options[option]))
            else:
                print("Unrecognised option %s" % option)
        else:
            option, value = line.split(" ", 1)
            if option not in self.options:
                print("Unrecognised option %s" % option)
                return
            elif value.isnumeric():
                value = int(value)
            elif value.lower() == "false":
                value = False
            elif value.lower() == "true":
                value = True
            self.options[option] = value
            if "auto_page" in option:
                print("""********************
WARNING!!!
********************
Support for auto_page functionality is being considered for DEPRECATION
to keep VF-1 small and simple.  If you are actually using this feature
and you don't want to see it removed, email solderpunk@sdf.org ASAP.
********************""")

    def do_handler(self, line):
        """View or set handler commands for different MIME types."""
        if not line.strip():
            # Show all current handlers
            for mime in sorted(_MIME_HANDLERS.keys()):
                print("%s   %s" % (mime, _MIME_HANDLERS[mime]))
        elif len(line.split()) == 1:
            mime = line.strip()
            if mime in _MIME_HANDLERS:
                print("%s   %s" % (mime, _MIME_HANDLERS[mime]))
            else:
                print("No handler set for MIME type %s" % mime)
        else:
            mime, handler = line.split(" ", 1)
            _MIME_HANDLERS[mime] = handler
            if "%s" not in handler:
                print("Are you sure you don't want to pass the filename to the handler?")

    ### Stuff for getting around
    def do_go(self, line):
        """Go to a gopher URL or marked item."""
        line = line.strip()
        if not line:
            print("Go where?")
        # First, check for possible marks
        elif line in self.marks:
            gi = self.marks[line]
            self._go_to_gi(gi)
        # or a local file
        elif os.path.exists(os.path.expanduser(line)):
            gi = GopherItem(None, None, os.path.expanduser(line),
                            "1", line, self.tls)
            self._go_to_gi(gi)
        # If this isn't a mark, treat it as a URL
        else:
            url = line
            if self.tls and url.startswith("gopher://"):
                print("Cannot enter demilitarized zone in battloid.")
                print("Use 'tls' to toggle battloid mode.")
                return
            elif not self.tls and url.startswith("gophers://"):
                print("Must use battloid mode to enter battlezone.")
                print("Use 'tls' to toggle battloid mode.")
                return
            elif not self.tls and not url.startswith("gopher://"):
                url = "gopher://" + url
            elif self.tls and not url.startswith("gophers://"):
                url = "gophers://" + url
            gi = url_to_gopheritem(url)
            self._go_to_gi(gi)

    def do_reload(self, *args):
        """Reload the current URL."""
        if self.gi:
            self._go_to_gi(self.gi)

    def do_up(self, *args):
        """Go up one directory in the path."""
        gi = self.gi
        if gi is None:
            print("There is no path without a gopher menu")
            return
        pathbits = os.path.split(self.gi.path)
        new_path = os.path.join(*pathbits[0:-1])
        new_gi = GopherItem(gi.host, gi.port, new_path, "1", gi.name, gi.tls)
        self._go_to_gi(new_gi, update_hist=False)

    def do_back(self, *args):
        """Go back to the previous gopher item."""
        if not self.history or self.hist_index == 0:
            return
        self.hist_index -= 1
        gi = self.history[self.hist_index]
        self._go_to_gi(gi, update_hist=False)

    def do_forward(self, *args):
        """Go forward to the next gopher item."""
        if not self.history or self.hist_index == len(self.history) - 1:
            return
        self.hist_index += 1
        gi = self.history[self.hist_index]
        self._go_to_gi(gi, update_hist=False)

    def do_next(self, *args):
        """Go to next item after current in index."""
        return self.onecmd(str(self.index_index+1))

    def do_previous(self, *args):
        """Go to previous item before current in index."""
        self.lookup = self.index
        return self.onecmd(str(self.index_index-1))

    def do_root(self, *args):
        """Go to root selector of the server hosting current item."""
        gi = GopherItem(self.gi.host, self.gi.port, "", "1",
                        "Root of %s" % self.gi.host, self.tls)
        self._go_to_gi(gi)

    def do_tour(self, line):
        """Add index items as waypoints on a tour, which is basically a FIFO
queue of gopher items.

Items can be added with `tour 1 2 3 4` or ranges like `tour 1-4`.
All items in current menu can be added with `tour *`.
Current tour can be listed with `tour ls` and scrubbed with `tour clear`."""
        line = line.strip()
        if not line:
            # Fly to next waypoint on tour
            if not self.waypoints:
                print("End of tour.")
            else:
                gi = self.waypoints.pop(0)
                self._go_to_gi(gi)
        elif line == "ls":
            old_lookup = self.lookup
            self.lookup = self.waypoints
            self.show_lookup()
            self.lookup = old_lookup
        elif line == "clear":
            self.waypoints = []
        elif line == "*":
            self.waypoints.extend(self.lookup)
        elif looks_like_url(line):
            self.waypoints.append(url_to_gopheritem(line))
        else:
            for index in line.split():
                try:
                    pair = index.split('-')
                    if len(pair) == 1:
                        # Just a single index
                        n = int(index)
                        gi = self.lookup[n-1]
                        self.waypoints.append(gi)
                    elif len(pair) == 2:
                        # Two endpoints for a range of indices
                        for n in range(int(pair[0]), int(pair[1]) + 1):
                            gi = self.lookup[n-1]
                            self.waypoints.append(gi)
                    else:
                        # Syntax error
                        print("Invalid use of range syntax %s, skipping" % index)
                except ValueError:
                    print("Non-numeric index %s, skipping." % index)
                except IndexError:
                    print("Invalid index %d, skipping." % n)

    def do_mark(self, line):
        """Mark the current item with a single letter.  This letter can then
be passed to the 'go' command to return to the current item later.
Think of it like marks in vi: 'mark a'='ma' and 'go a'=''a'."""
        if not self.gi:
            print("You need to 'go' somewhere, first")
        line = line.strip()
        if not line:
            for mark, gi in self.marks.items():
                print("[%s] %s (%s)" % (mark, gi.name, gopheritem_to_url(gi)))
        elif line.isalpha() and len(line) == 1:
            self.marks[line] = self.gi
        else:
            print("Invalid mark, must be one letter")

    def do_veronica(self, line):
        # Don't tell Betty!
        """Submit a search query to the Veronica 2 search engine."""
        f = send_query("/v2/vs", line, "gopher.floodgap.com", 70)
        f = self._decode_text(f)
        self._handle_index(f)

    ### Stuff that modifies the lookup table
    def do_ls(self, line):
        """List contents of current index.
Use 'ls -l' to see URLs.
Use 'ls -r' to list in reverse order."""
        self.lookup = self.index
        # If we add any more options to ls, we'll have to use argparse
        # again here, but I hope the options don't explode, and for just
        # two, the below seems good enough.
        options = line.strip().split()
        show_urls = any((x in options for x in ("-l", "-lr")))
        reverse = any((x in options for x in ("-r", "-lr")))
        if reverse:
            print("""********************
WARNING!!!
********************
Support for reverse ls functionality is being considered for DEPRECATION
to keep VF-1 small and simple.  If you are actually using this feature
and you don't want to see it removed, email solderpunk@sdf.org ASAP.
********************""")
        self.show_lookup(url = show_urls, reverse = reverse)
        self.page_index = 0

    def do_history(self, *args):
        """Display history."""
        self.lookup = self.history
        self.show_lookup(url=True)

    def do_search(self, searchterm):
        """Search index (case insensitive)."""
        results = [
            gi for gi in self.lookup if searchterm.lower() in gi.name.lower()]
        if results:
            self.lookup = results
            self.show_lookup()
        else:
            print("No results found.")

    def emptyline(self):
        """Page through index ten lines at a time."""
        i = self.page_index
        if i > len(self.lookup):
            return
        self.show_lookup(offset=i, end=i+10)
        self.page_index += 10

    ### Stuff that does something to most recently viewed item
    def do_cat(self, *args):
        """Run most recently visited item through "cat" command."""
        subprocess.call(shlex.split("cat %s" % self._get_active_tmpfile()))

    def do_less(self, *args):
        """Run most recently visited item through "less" command."""
        cmd_str = self.get_handler_cmd(self.gi)
        cmd_str = cmd_str % self._get_active_tmpfile()
        subprocess.call("%s | less -R" % cmd_str, shell=True)

    def do_fold(self, *args):
        """Run most recently visited item through "fold" command."""
        cmd_str = self.get_handler_cmd(self.gi)
        cmd_str = cmd_str % self._get_active_tmpfile()
        subprocess.call("%s | fold -w 70 -s" % cmd_str, shell=True)

    def do_shell(self, line):
        """'cat' most recently visited item through a shell pipeline."""
        subprocess.call(("cat %s |" % self._get_active_tmpfile()) + line, shell=True)

    def do_save(self, filename):
        """Save most recently visited item to file."""
        filename = os.path.expanduser(filename)
        if not filename:
            print("Please provide a filename")
        elif not self.tmp_filename:
            print("You need to visit a text item, first")
        elif os.path.exists(filename):
            print("File already exists!")
        else:
            # Don't use _get_active_tmpfile() here, because we want to save the
            # "source code" of the menu, not the rendered view - this way VF-1
            # can navigate to it later.
            shutil.copyfile(self.tmp_filename, filename)

    def do_url(self, *args):
        """Print URL of most recently visited item."""
        print(gopheritem_to_url(self.gi))

    def do_links(self, *args):
        """Extract URLs from most recently visited item."""
        if self.gi.itemtype not in ("0", "h"):
            print("You need to visit a text item, first")
            return
        links = []
        with open(self.tmp_filename, "r") as fp:
            for line in fp:
                words = line.strip().split()
                links.extend([url_to_gopheritem(w) for w in words if looks_like_url(w)])
        self.lookup = links
        self.show_lookup(name=False, url=True)

    ### Bookmarking stuff
    def do_add(self, line):
        """Add the current URL to the bookmarks menu.
Bookmarks are stored in the ~/.vf1-bookmarks.txt file.
Optionally, specify the new name for the bookmark."""
        if not self.gi:
            print("You need to 'go' somewhere, first")
        else:
            with open(os.path.expanduser("~/.vf1-bookmarks.txt"), "a") as fp:
                fp.write(gopheritem_to_line(self.gi, name=line))

    def do_bookmarks(self, *args):
        """Show the current bookmarks menu.
Bookmarks are stored in the ~/.vf1-bookmarks.txt file."""
        file_name = "~/.vf1-bookmarks.txt"
        if not os.path.isfile(os.path.expanduser(file_name)):
            print("You need to 'add' some bookmarks, first")
        else:
            gi = GopherItem(None, None, os.path.expanduser(file_name),
                            "1", file_name, self.tls)
            self._go_to_gi(gi)

    ### Security
    def do_tls(self, *args):
        """Engage or disengage battloid mode."""
        self.set_prompt(not self.tls)
        if self.tls:
            print("Battloid mode engaged! Only accepting encrypted connections.")
        else:
            print("Battloid mode disengaged! Switching to unencrypted channels.")

    ### The end!
    def do_quit(self, *args):
        """Exit VF-1."""
        # Clean up after ourself
        if self.tmp_filename:
            os.unlink(self.tmp_filename)
        if self.idx_filename:
            os.unlink(self.idx_filename)
        print()
        print("Thank you for flying VF-1!")
        sys.exit()

    do_exit = do_quit

# Code below is the core of he gopherlib.py module from Python 2.4, with
# minimal changes made for Python 3 compatibility and to handle
# convenient download of plain text (including Unicode) or binary files.

# Default selector, host and port
DEF_PORT     = 70

# Names for characters and strings
CRLF = '\r\n'

def send_selector(selector, host, port = 0, tls=False):
    """Send a selector to a given host and port.
Returns a binary file with the reply."""
    if not port:
        i = host.find(':')
        if i >= 0:
            host, port = host[:i], int(host[i+1:])
    if not port:
        port = DEF_PORT
    elif type(port) == type(''):
        port = int(port)
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if tls:
        context = ssl.create_default_context()
        # context.check_hostname = False
        # context.verify_mode = ssl.CERT_NONE
        s = context.wrap_socket(s, server_hostname = host)
    else:
        s.settimeout(10.0)
    s.connect((host, port))
    s.sendall((selector + CRLF).encode("UTF-8"))
    return s.makefile(mode = "rb")

def send_query(selector, query, host, port=0, tls=False):
    """Send a selector and a query string."""
    return send_selector(selector + '\t' + query, host, port, tls)

# Config file finder
def get_rcfile():
    rc_paths = ("~/.config/vf1/vf1rc", "~/.config/.vf1rc", "~/.vf1rc")
    for rc_path in rc_paths:
        rcfile = os.path.expanduser(rc_path)
        if os.path.exists(rcfile):
            return rcfile
    return None

# Main function
def main():

    # Parse args
    parser = argparse.ArgumentParser(description='A command line gopher client.')
    parser.add_argument('--bookmarks', action='store_true',
                        help='start with your list of bookmarks')
    parser.add_argument('url', metavar='URL', nargs='*',
                        help='start with this URL')
    parser.add_argument('--tls', action='store_true',
                        help='secure all communications using TLS')
    args = parser.parse_args()

    # Instantiate client
    gc = GopherClient(tls=args.tls)

    # Process config file
    rcfile = get_rcfile()
    if rcfile:
        print("Using config %s" % rcfile)
        with open(rcfile, "r") as fp:
            for line in fp:
                line = line.strip()
                if ((args.bookmarks or args.url) and
                    any((line.startswith(x) for x in ("go", "g", "tour", "t")))
                   ):
                    if args.bookmarks:
                        print("Skipping rc command \"%s\" due to --bookmarks option." % line)
                    else:
                        print("Skipping rc command \"%s\" due to provided URLs." % line)
                    continue
                gc.cmdqueue.append(line)

    # Say hi
    print("Welcome to VF-1!")
    if args.tls:
        print("Battloid mode engaged! Watch your back in Gopherspace!")
    else:
        print("Enjoy your flight through Gopherspace...")

    # Act on args
    if args.bookmarks:
        gc.do_bookmarks()
    elif args.url:
        if len(args.url) == 1:
            gc.do_go(args.url[0])
        else:
            for url in args.url:
                if not url.startswith("gopher://"):
                    url = "gopher://" + url
                gc.do_tour(url)
            gc.do_tour("")

    # Endless interpret loop
    while True:
        try:
            gc.cmdloop()
        except KeyboardInterrupt:
            print("")

if __name__ == '__main__':
    main()
