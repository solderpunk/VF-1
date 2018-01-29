#!/usr/bin/env python3
# VF-1 Gopher client
# (C) 2018 Solderpunk <solderpunk@sdf.org>
# With contributions from:
#  - Alex Schroeder <alex@gnu.org>

import argparse
import cmd
import collections
import fnmatch
import io
import mimetypes
import os.path
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import traceback
import urllib.parse

# Command abbreviations
_ABBREVS = {
    "a":    "add",
    "b":    "back",
    "bm":   "bookmarks",
    "book": "bookmarks",
    "f":    "fold",
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
    "v":    "veronica",
}

# Programs to handle different item types
_ITEMTYPE_TO_MIME = {
    "0":    "text/plain",
    "h":    "text/html",
    "g":    "image/gif",
    "I":    "image/jpeg",
    "s":    "audio/x-wav",
}

_MIME_HANDLERS = {
    "text/plain":           "cat %s",
    "text/html":            "lynx --dump %s",
    "image/*":              "feh %s",
    "audio/*":              "mpg123 %s",
    "application/pdf":      "xpdf %s",
}

# Lightweight representation of an item in Gopherspace
GopherItem = collections.namedtuple("GopherItem",
        ("host", "port", "path", "itemtype", "name"))

def url_to_gopheritem(url, itemtype="?"):
    u = urllib.parse.urlparse(url)
    # https://tools.ietf.org/html/rfc4266#section-2.1
    path = u.path
    if u.path and u.path[0] == '/' and len(u.path) > 1:
        itemtype = u.path[1]
        path = u.path[2:]
    return GopherItem(u.hostname, u.port or 70, path, str(itemtype),
                      "<direct URL>")

def gopheritem_to_url(gi):
    if gi:
        return ("gopher://%s:%d/%s%s" % (gi.host, int(gi.port),
                                         gi.itemtype, gi.path))
    else:
        return ""

def gopheritem_from_line(line):
    line = line.strip()
    name, path, server, port = line.split("\t")
    itemtype = name[0]
    name = name[1:]
    return GopherItem(server, port, path, itemtype, name)

def gopheritem_to_line(gi, name=""):
    # Prepend itemtype to name
    name = str(gi.itemtype) + (name or gi.name)
    return "\t".join((name, gi.path, gi.host, str(gi.port))) + "\n"

# Cheap and cheerful URL detector
def looks_like_url(word):
    return "." in word and word.startswith("gopher://")

class GopherClient(cmd.Cmd):

    def __init__(self):
        cmd.Cmd.__init__(self)
        self.prompt = "\x1b[38;5;202m" + "VF-1" + "\x1b[38;5;255m" + "> " + "\x1b[0m"
        self.tmp_filename = ""
        self.index = []
        self.index_index = -1
        self.history = []
        self.hist_index = 0
        self.page_index = 0
        self.lookup = self.index
        self.gi = None
        self.pwd = None
        self.waypoints = []
        self.marks = {}

    def _go_to_gi(self, gi, update_hist=True):
        # Hit the network
        try:
            # Is this a search point?
            if gi.itemtype == "7":
                query_str = input("Query term: ")
                f = send_query(gi.path, query_str, gi.host, gi.port or 70)
            else:
                # Use gopherlib to create a binary file handler
                f = send_selector(gi.path, gi.host, gi.port or 70)
        except socket.gaierror:
            print("ERROR: DNS error!")
            return
        except ConnectionRefusedError:
            print("ERROR: Connection refused!")
            return
        except TimeoutError:
            print("ERROR: Connection timed out!")
            return

        # Attempt to decode something that is supposed to be text
        if gi.itemtype in ("0", "1", "7", "h"):
            try:
                f = self._decode_text(f)
            except UnicodeError:
                print("ERROR: Unsupported text encoding!")
                return

        # Take a best guess at items with unknown type (for example
        # when using go example.com and no path)
        elif gi.itemtype == "?":
            gi, f = self._autodetect_itemtype(gi, f)

        # Process that file handler depending upon itemtype
        if gi.itemtype == "1":
            self._handle_index(f)
            self.pwd = gi
        elif gi.itemtype == "7":
            self._handle_index(f)
            # Return now so we don't update any further state
            return
        else:
            if self.tmp_filename:
                os.unlink(self.tmp_filename)

            # Set mode for tmpfile
            if gi.itemtype in ("0", "h"):
                mode = "w"
                encoding = "UTF-8"
            else:
                mode = "wb"
                encoding = None

            tmpf = tempfile.NamedTemporaryFile(mode, encoding=encoding, delete=False)
            tmpf.write(f.read())
            tmpf.close()
            self.tmp_filename = tmpf.name

            cmd_str = self.get_handler_cmd(gi)
            try:
                subprocess.call(shlex.split(cmd_str % tmpf.name))
            except FileNotFoundError:
                print("Handler program %s not found!" % cmd_str.split()[0])
                print("You can use the ! command to specify another handler program or pipeline.")
      
        # Update state
        if update_hist:
            self._update_history(gi)
        self.gi = gi

    def get_handler_cmd(self, gi):
        if gi.itemtype in _ITEMTYPE_TO_MIME:
            mimetype = _ITEMTYPE_TO_MIME[gi.itemtype]
        else:
            mimetype, encoding = mimetypes.guess_type(gi.path)
        for handled_mime, cmd_str in _MIME_HANDLERS.items():
            if fnmatch.fnmatch(mimetype, handled_mime):
                break
        else:
            cmd_str = "strings %d"
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

    def _autodetect_itemtype(self, gi, f):
        # INPUT: gi, f
        # gi is a GopherItem with an itemtype of ?
        # f is a non-seekable filelike item returning raw bytes
        # OUTPUT: gi, f
        # gi is a GopherItem with known itemtype
        # f is a seekable filelike item returning Unicode if gi.itemtype
        # is 0 or 1, or returning raw bytes otherwise

        raw_bytes = io.BytesIO(f.read())
        try:
            text = self._decode_text(raw_bytes)
        except UnicodeError:
            raw_bytes.seek(0)
            new_gi = GopherItem(gi.host, gi.port, gi.path, "9", gi.name)
            return new_gi, raw_bytes

        # If we're here, we know we got text
        # Is this an index?
        hits = 0
        for n, line in enumerate(text.readlines()):
            if n == 10:
                break
            try:
                junk_gi = gopheritem_from_line(line)
                hits += 1
            except:
                continue
        if hits:
            new_gi = GopherItem(gi.host, gi.port, gi.path, "1", gi.name)
        else:
            new_gi = GopherItem(gi.host, gi.port, gi.path, "0", gi.name)
        text.seek(0)
        return new_gi, text

    def _handle_index(self, f):
        self.index = []
        n = 1
        for line in f:
            if len(line.split("\t")) != 4:
                continue
            if line.startswith("3"):
                print("Error message from server:")
                print(line[1:].split("\t")[0])
                return
            elif line.startswith("i"):
                print(line[1:].split("\t")[0])
            else:
                gi = gopheritem_from_line(line)
                print(("[%d] " % n) + gi.name)
                self.index.append(gi)
                n += 1
        self.lookup = self.index
        self.index_index = -1
        self.page_index = 0

    def _update_history(self, gi):
        # Don't duplicate
        if self.history and self.history[-1] == gi:
            return
        self.history.append(gi)
        self.hist_index = len(self.history) - 1

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
        # If this isn't a mark, treat it as a URL
        else:
            url = line
            if not url.startswith("gopher://"):
                url = "gopher://" + url
            gi = url_to_gopheritem(url, "?")
            self._go_to_gi(gi)

    def do_reload(self, *args):
        """Reload the current URL."""
        if self.gi:
            self._go_to_gi(self.gi)

    def do_up(self, *args):
        """Go up one directory in the path."""
        pwd = self.pwd
        pathbits = os.path.split(pwd.path)
        newpath = os.path.join(*pathbits[0:-1])
        gi = GopherItem(pwd.host, pwd.port, newpath, pwd.itemtype, pwd.name)
        self._go_to_gi(gi, update_hist=False)

    def do_back(self, *args):
        """Go back to the previous gopher item."""
        if not self.history:
            return
        self.hist_index -= 1
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
        gi = GopherItem(self.gi.host, self.gi.port, "", "?", "Root of %s" % self.gi.host)
        self._go_to_gi(gi)

    def do_tour(self, line):
        """Add index items as waypoints on a tour, which is basically
a FIFO queue of gopher items."""
        if not line:
            # Fly to next waypoint on tour
            if not self.waypoints:
                print("End of tour.")
            else:
                gi = self.waypoints.pop(0)
                self._go_to_gi(gi)
        else:
            for index in line.strip().split():
                try:
                    n = int(index)
                    gi = self.lookup[n-1]
                    self.waypoints.append(gi)
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
    def do_ls(self, *args):
        """List contents of current index."""
        self.lookup = self.index
        self.show_lookup()

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

    def show_lookup(self, offset=0, end=None, name=True, url=False):
        for n, gi in enumerate(self.lookup[offset:end]):
            n += offset
            line = "[%d] " % (n+1)
            if name:
                line += gi.name + " "
            if url:
                line += "(%s)" % gopheritem_to_url(gi)
            print(line)

    ### Stuff that does something to most recently viewed item
    def do_less(self, *args):
        """Run most recently visited item through "less" command."""
        subprocess.call(shlex.split("less %s" % self.tmp_filename))

    def do_fold(self, *args):
        """Run most recently visited item through "fold" command."""
        subprocess.call(shlex.split("fold -w 80 -s %s" % self.tmp_filename))

    def do_shell(self, line):
        """'cat' most recently visited item through a shell pipeline."""
        subprocess.call(("cat %s |" % self.tmp_filename) + line, shell=True)

    def do_save(self, filename):
        """Save most recently visited item to file."""
        if os.path.exists(filename):
            print("File already exists!")
        else:
            shutil.copyfile(self.tmp_filename, filename)

    def do_url(self, *args):
        """Print URL of most recently visited item."""
        print(gopheritem_to_url(self.gi))

    def do_links(self, *args):
        """Extract URLs from most recently visited item."""
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
            with open(os.path.expanduser(file_name), "r") as fp:
                self._handle_index(fp)

    ### The end!
    def do_quit(self, *args):
        """Exit VF-1."""
        # Clean up after ourself
        if self.tmp_filename:
            os.unlink(self.tmp_filename)
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

def send_selector(selector, host, port = 0):
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
    s.connect((host, port))
    s.sendall((selector + CRLF).encode("UTF-8"))
    return s.makefile(mode = "rb")

def send_query(selector, query, host, port = 0):
    """Send a selector and a query string."""
    return send_selector(selector + '\t' + query, host, port)

# Main function
def main():

    parser = argparse.ArgumentParser(description='A command line gopher client.')
    parser.add_argument('--bookmarks', action='store_true',
                        help='start with your list of bookmarks')
    parser.add_argument('url', metavar='URL', nargs='?',
                        help='start with this URL')
    args = parser.parse_args()

    gc = GopherClient()
    print("Welcome to VF-1!")
    print("Enjoy your flight through Gopherspace...")
    rcfile = os.path.expanduser("~/.vf1rc")
    if os.path.exists(rcfile):
        with open(rcfile, "r") as fp:
            gc.cmdqueue = fp.readlines()
    if args.bookmarks:
        gc.do_bookmarks()
    elif args.url:
        gc.do_go(args.url)
    while True:
        try:
            gc.cmdloop()
        except KeyboardInterrupt:
            print("")

if __name__ == '__main__':
    main()
