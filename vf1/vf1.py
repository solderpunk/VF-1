# VF-1 Gopher client
# Prototype release

import cmd
import collections
import io
import os.path
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import traceback
import urllib.parse
import signal

def signal_handler(signal, frame):
    print("BREAK")

signal.signal(signal.SIGINT, signal_handler)

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
    "t":    "tour",
    "se":   "search",
    "/":    "search",
}

# Programs to handle different item types
_HANDLERS = {
    "0":    "cat %s",
    "h":    "lynx --dump %s",
    "g":    "feh %s",
    "s":    "mpg123 %s",
}
_HANDLERS["I"] = _HANDLERS["g"]

# Lightweight representation of an item in Gopherspace
GopherItem = collections.namedtuple("GopherItem",
        ("host", "port", "path", "itemtype", "name"))

def url_to_gopheritem(url, itemtype="?"):
    u = urllib.parse.urlparse(url)
    return GopherItem(u.hostname, u.port or 70, u.path, str(itemtype),
            "<direct URL>")

def gopheritem_to_url(gi):
    return ("gopher://%s:%d/%s" % (gi.host, int(gi.port), gi.path)) if gi else ""

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
    return "://" in word and word.count(".") > 0

class GopherClient(cmd.Cmd):

    def __init__(self):
        cmd.Cmd.__init__(self)
        self.intro = "Welcome to VF-1!\nEnjoy your flight through Gopherspace..."
        self.prompt = "VF-1> "
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
                self._handle_index(f)
                return

            # Use gopherlib to create a file handler (binary or text)
            if gi.itemtype in ("?", "g", "I", "s", "9"):
                mode = "rb"
            else:
                mode = "r"
            f = send_selector(gi.path, gi.host, gi.port or 70, mode)
        except socket.gaierror:
            print("ERROR: DNS error!")
            return
        except ConnectionRefusedError:
            print("ERROR: Connection refused!")
            return
        except TimeoutError:
            print("ERROR: Connection timed out!")
            return

        # Take a best guess at items with unknown type
        if gi.itemtype == "?":
            gi, f = self._autodetect_itemtype(gi, f)
            if gi.itemtype in ("?", "g", "I", "s", "9"):
                mode = "rb"
            else:
                mode = "r"

        # Process that file handler depending upon itemtype
        if gi.itemtype == "1":
            self._handle_index(f)
            self.pwd = gi
        else:
            if self.tmp_filename:
                os.unlink(self.tmp_filename)
            tmpf = tempfile.NamedTemporaryFile("w" if mode == "r" else "wb", delete=False)
            tmpf.write(f.read())
            tmpf.close()
            self.tmp_filename = tmpf.name

            cmd_str = _HANDLERS.get(gi.itemtype, "strings %s")
            subprocess.run(shlex.split(cmd_str % tmpf.name))
      
        # Update state
        if update_hist:
            self._update_history(gi)
        self.gi = gi

    def _autodetect_itemtype(self, gi, f):
        raw_bytes = f.read()
        # Is this text?
        try:
            text = raw_bytes.decode("UTF-8")
        except UnicodeError:
            new_f = io.BytesIO()
            new_f.write(raw_bytes)
            new_f.seek(0)
            new_gi = GopherItem(gi.host, gi.port, gi.path, "9", gi.name)
            return new_gi, new_f

        # If we're here, we know we got text
        new_f = io.StringIO()
        new_f.write(text)
        new_f.seek(0)
        # Is this an index?
        hits = 0
        for n, line in enumerate(new_f.readlines()):
            if n == 10:
                break
            try:
                gi = gopheritem_from_line(line)
                hits += 1
            except:
                continue
        if hits:
            new_gi = GopherItem(gi.host, gi.port, gi.path, "1", gi.name)
        else:
            new_gi = GopherItem(gi.host, gi.port, gi.path, "0", gi.name)
        new_f.seek(0)
        return new_gi, new_f

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

    ### Stuff for getting around
    def do_go(self, line):
        """Go to a gopher URL or marked item."""
        line = line.strip()
        # First, check for possible marks
        if line in self.marks:
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
        self.lookup = [
            gi for gi in self.lookup if searchterm.lower() in gi.name.lower()]
        self.show_lookup()

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
        subprocess.run(shlex.split("less %s" % self.tmp_filename))

    def do_fold(self, *args):
        """Run most recently visited item through "fold" command."""
        subprocess.run(shlex.split("fold -w 80 -s %s" % self.tmp_filename))

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
TAB = '\t'

def send_selector(selector, host, port = 0, mode="r"):
    """Send a selector to a given host and port, return a file with the reply."""
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
    s.shutdown(1)
    return s.makefile(mode, encoding="UTF-8")

def send_query(selector, query, host, port = 0):
    """Send a selector and a query string."""
    return send_selector(selector + '\t' + query, host, port, "r")

if __name__ == '__main__':
    gc = GopherClient()
    gc.cmdloop()
