#!/usr/bin/env python3
# VF-1 Gopher client
# (C) 2018,2019 Solderpunk <solderpunk@sdf.org>
# With contributions from:
#  - Alex Schroeder <alex@gnu.org>
#  - Joseph Lyman <tfurrows@sdf.org>
#  - Adam Mayer (https://github.com/phooky)
#  - Paco Esteban <paco@onna.be>

import argparse
import cmd
import codecs
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
import urllib.parse
import ssl
import time

# Use chardet if it's there, but don't depend on it
try:
    import chardet
    _HAS_CHARDET = True
except ImportError:
    _HAS_CHARDET = False

# Command abbreviations
_ABBREVS = {
    "a":    "add",
    "b":    "back",
    "bb":   "blackbox",
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
    "text/*":               "cat %s",
    "text/html":            "lynx -dump -force_html %s",
}

# Item type formatting stuff
_ITEMTYPE_TITLES = {
    "7":        " <INP>",
    "8":        " <TEL>",
    "9":        " <BIN>",
    "h":        " <HTM>",
    "g":        " <IMG>",
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

CRLF = '\r\n'

# Lightweight representation of an item in Gopherspace
GopherItem = collections.namedtuple("GopherItem",
        ("host", "port", "path", "itemtype", "name", "tls"))

def url_to_gopheritem(url, tls=False):
    # urllibparse.urlparse can handle IPv6 addresses, but only if they
    # are formatted very carefully, in a way that users almost
    # certainly won't expect.  So, catch them early and try to fix
    # them...
    if url.count(":") > 2: # Best way to detect them?
        url = fix_ipv6_url(url)
    # Prepend a gopher schema if none given
    if "://" not in url:
        url = ("gophers://" if tls else "gopher://") + url
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
                      str(itemtype), "",
                      True if u.scheme == "gophers" else False)

def fix_ipv6_url(url):
    # If there's a pair of []s in there, it's probably fine as is.
    if "[" in url and "]" in url:
        return url
    # Easiest case is a raw address, no schema, no path.
    # Just wrap it in square brackets and whack a slash on the end
    if "/" not in url:
        return "[" + url + "]/"
    # Now the trickier cases...
    if "://" in url:
        schema, schemaless = url.split("://")
    else:
        schema, schemaless = None, url
    if "/" in schemaless:
        netloc, rest = schemaless.split("/",1)
        schemaless = "[" + netloc + "]" + "/" + rest
    if schema:
        return schema + "://" + schemaless
    return schemaless

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

def gopheritem_from_line(line, tls_host, tls_port):
    # Split on tabs.  Strip final element after splitting,
    # since if we split first we loose empty elements.
    parts = line.split("\t")
    parts[-1] = parts[-1].strip()
    # Discard Gopher+ noise
    if parts[-1] == "+":
        parts = parts[:-1]
    # Attempt to assign variables.  This may fail.
    # It's up to the caller to catch the Exception.
    name, path, host, port = parts
    itemtype = name[0]
    name = name[1:]
    port = int(port)
    # Handle the h-type URL: hack for secure links
    if itemtype == "h" and path.startswith("URL:gopher"):
        url = path[4:]
        return url_to_gopheritem(url)
    return GopherItem(host, port, path, itemtype, name,
            host == tls_host and port == tls_port)

def gopheritem_to_line(gi, name=""):
    name = ((name or gi.name) or gopheritem_to_url(gi))
    # Prepend itemtype to name
    if gi.tls:
        # Use h-type URL: for secure links
        name = "h" + name
        path = "URL:" + gopheritem_to_url(gi)
    else:
        name = str(gi.itemtype) + name
        path = gi.path
    return "\t".join((name, path, gi.host or "", str(gi.port))) + "\n"

# Cheap and cheerful URL detector
def looks_like_url(word):
    return "." in word and ("gopher://" in word or "gophers://" in word)

def extract_url(word):
    # Given a word that probably contains a URL, extract that URL from
    # with sensible surrounding punctuation.
    print(word)
    for start, end in (("<",">"), ('[',']'), ("(",")"), ("'","'"), ('"','"')):
        print(word[0], start)
        if word[0] == start and end in word:
            return word[1:word.rfind(end)]
    if word.endswith("."):
        return word[:-1]
    else:
        return word

# Decorators
def needs_gi(inner):
    def outer(self, *args, **kwargs):
        if not self.gi:
            print("You need to 'go' somewhere, first")
            return None
        else:
            return inner(self, *args, **kwargs)
    outer.__doc__ = inner.__doc__
    return outer

class GopherClient(cmd.Cmd):

    def __init__(self, debug=False, tls=False):
        cmd.Cmd.__init__(self)
        self._set_tls(tls)
        self.gi = None
        self.history = []
        self.hist_index = 0
        self.menu_filename = ""
        self.menu = []
        self.menu_index = -1
        self.lookup = self.menu
        self.marks = {}
        self.mirrors = {}
        self.page_index = 0
        self.tmp_filename = ""
        self.visited_hosts = set()
        self.waypoints = []

        self.options = {
            "color_menus" : False,
            "debug" : debug,
            "encoding" : "iso-8859-1",
            "ipv6" : True,
            "timeout" : 10,
        }

        self.log = {
            "start_time": time.time(),
            "requests": 0,
            "tls_requests": 0,
            "ipv4_requests": 0,
            "ipv6_requests": 0,
            "bytes_recvd": 0,
            "ipv4_bytes_recvd": 0,
            "ipv6_bytes_recvd": 0,
            "dns_failures": 0,
            "refused_connections": 0,
            "reset_connections": 0,
            "timeouts": 0,
        }
        self.itemtype_counts = { itemtype: 0 for itemtype in "0 1 7 8 9 h g s I T p".split()}

    def _go_to_gi(self, gi, update_hist=True, query_str=None, handle=True):
        """This method might be considered "the heart of VF-1".
        Everything involved in fetching a gopher resource happens here:
        sending the request over the network, parsing the response if
        its a menu, storing the response in a temporary file, choosing
        and calling a handler program, and updating the history."""
        # Handle non-gopher protocols first
        if gi.itemtype in ("8", "T", "S"):
            # SSH (non-standard, but nice)
            if gi.itemtype == "S":
                subprocess.call(shlex.split("ssh %s@%s -p %s" % (gi.path, gi.host, gi.port)))
            # Telnet
            elif gi.path:
                subprocess.call(shlex.split("telnet -l %s %s %s" % (gi.path, gi.host, gi.port)))
            else:
                subprocess.call(shlex.split("telnet %s %s" % (gi.host, gi.port)))
            if update_hist:
                self._update_history(gi)
            return

        # From here on in, it's gopher only

        # Enforce "no surprises" policy re: crypto
        if self.tls and not gi.tls:
            print("Cannot enter demilitarized zone in battloid!")
            print("Use 'tls' to disable battloid mode before leaving encrypted gopherspace.")
            return
        elif not self.tls and gi.tls:
            self._set_tls(True)
            print("Enabling battloid mode to connect to encrypted server.")

        # Do everything which touches the network in one block,
        # so we only need to catch exceptions once
        try:
            # Is this a local file?
            if not gi.host:
                address, f = None, open(gi.path, "rb")
            # Is this a search point?
            elif gi.itemtype == "7":
                if not query_str:
                    query_str = input("Query term: ")
                address, f = self._send_request(gi, query=query_str)
            else:
                address, f = self._send_request(gi)
            # Read whole response
            response = f.read()
            f.close()

        # Catch network errors which may be recoverable if a redundant
        # mirror is specified
        except (socket.gaierror, ConnectionRefusedError,
                ConnectionResetError, TimeoutError, socket.timeout,
                ) as network_error:
            # Print an error message
            if isinstance(network_error, socket.gaierror):
                self.log["dns_failures"] += 1
                print("ERROR: DNS error!")
            elif isinstance(network_error, ConnectionRefusedError):
                self.log["refused_connections"] += 1
                print("ERROR: Connection refused!")
            elif isinstance(network_error, ConnectionResetError):
                self.log["reset_connections"] += 1
                print("ERROR: Connection reset!")
            elif isinstance(network_error, (TimeoutError, socket.timeout)):
                self.log["timeouts"] += 1
                print("""ERROR: Connection timed out!
Slow internet connection?  Use 'set timeout' to be more patient.""")
                if not self.tls:
                    print("Encrypted gopher server?  Use 'tls' to enable encryption.")
            # Try to fall back on a redundant mirror
            new_gi = self._get_mirror_gi(gi)
            if new_gi:
                print("Trying redundant mirror %s..." % gopheritem_to_url(new_gi))
                self._go_to_gi(new_gi)
            return
        # Catch non-recoverable errors
        except Exception as err:
            print("ERROR: " + str(err))
            if isinstance(err, ssl.SSLError):
                print(gopheritem_to_url(gi) + " is probably not encrypted.")
                print("Use 'tls' to disable encryption.")
            return

        # Attempt to decode something that is supposed to be text
        if gi.itemtype in ("0", "1", "7", "h"):
            try:
                response = self._decode_text(response)
            except UnicodeError:
                print("""ERROR: Unknown text encoding!
If you know the correct encoding, use e.g. 'set encoding koi8-r' and
try again.  Otherwise, install the 'chardet' library for Python 3 to
enable automatic encoding detection.""")
                return

        # Render gopher menus
        if gi.itemtype in ("1", "7"):
            response = self._render_menu(response, gi)

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
        size = tmpf.write(response)
        tmpf.close()
        self.tmp_filename = tmpf.name
        self._debug("Wrote %d byte response to %s." % (size, self.tmp_filename))

        # Pass file to handler, unless we were asked not to
        if handle:
            cmd_str = self._get_handler_cmd(gi)
            try:
                subprocess.call(cmd_str % tmpf.name, shell=True)
            except FileNotFoundError:
                print("Handler program %s not found!" % shlex.split(cmd_str)[0])
                print("You can use the ! command to specify another handler program or pipeline.")

        # Update state
        self.gi = gi
        self._log_visit(gi, address, size)
        if update_hist:
            self._update_history(gi)

    # This method below started life as the core of the old gopherlib.py
    # module from Python 2.4, with minimal changes made for Python 3
    # compatibility and to handle convenient download of plain text (including
    # Unicode) or binary files.  It's come a long way since then, though.
    # Everything network related happens in this one method!
    def _send_request(self, gi, query=None):
        """Send a selector to a given host and port.
        Returns the resolved address and binary file with the reply."""
        # Add query to selector
        if query:
            gi = gi._replace(path=gi.path + "\t" + query)
        # DNS lookup - will get IPv4 and IPv6 records if IPv6 is enabled
        if ":" in gi.host:
            # This is likely a literal IPv6 address, so we can *only* ask for
            # IPv6 addresses or getaddrinfo will complain
            family_mask = socket.AF_INET6
        elif socket.has_ipv6 and self.options["ipv6"]:
            # Accept either IPv4 or IPv6 addresses
            family_mask = 0
        else:
            # IPv4 only
            family_mask = socket.AF_INET
        addresses = socket.getaddrinfo(gi.host, gi.port, family=family_mask,
                type=socket.SOCK_STREAM)
        # Sort addresses so IPv6 ones come first
        addresses.sort(key=lambda add: add[0] == socket.AF_INET6, reverse=True)
        # Verify that this sort works
        if any(add[0] == socket.AF_INET6 for add in addresses):
            assert addresses[0][0] == socket.AF_INET6
        # Connect to remote host by any address possible
        err = None
        for address in addresses:
            self._debug("Connecting to: " + str(address[4]))
            s = socket.socket(address[0], address[1])
            s.settimeout(self.options["timeout"])
            if self.tls:
                context = ssl.create_default_context()
                # context.check_hostname = False
                # context.verify_mode = ssl.CERT_NONE
                s = context.wrap_socket(s, server_hostname = gi.host)
            try:
                s.connect(address[4])
                break
            except OSError as e:
                err = e
        else:
            # If we couldn't connect to *any* of the addresses, just
            # bubble up the exception from the last attempt and deny
            # knowledge of earlier failures.
            raise err
        # Send request and wrap response in a file descriptor
        self._debug("Sending %s<CRLF>" % gi.path)
        s.sendall((gi.path + CRLF).encode("UTF-8"))
        return address, s.makefile(mode = "rb")

    def _get_handler_cmd(self, gi):
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
        self._debug("Assigned MIME type: %s" % mimetype)

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
            # Use "xdg-open" as a last resort.
            cmd_str = "xdg-open %s"
        self._debug("Using handler: %s" % cmd_str)
        return cmd_str

    def _decode_text(self, raw_bytes):
        # Attempt to decode some bytes into a Unicode string.
        # First of all, try UTF-8 as the default.
        # If this fails, attempt to autodetect the encoding if chardet
        # library is installed.
        # If chardet is not installed, or fails to work, fall back on
        # the user-specified alternate encoding.
        # If none of this works, this will raise UnicodeError and it's
        # up to the caller to handle it gracefully.
        # Try UTF-8 first:
        try:
            text = raw_bytes.decode("UTF-8")
        except UnicodeError:
            # If we have chardet, try the magic
            self._debug("Could not decode response as UTF-8.")
            if _HAS_CHARDET:
                autodetect = chardet.detect(raw_bytes)
                # Make sure we're vaguely certain
                if autodetect["confidence"] > 0.5:
                    self._debug("Trying encoding %s as recommended by chardet." % autodetect["encoding"])
                    text = raw_bytes.decode(autodetect["encoding"])
                else:
                    # Try the user-specified encoding
                    self._debug("Trying fallback encoding %s." % self.options["encoding"])
                    text = raw_bytes.decode(self.options["encoding"])
            else:
                # Try the user-specified encoding
                text = raw_bytes.decode(self.options["encoding"])
        if not text.endswith("\n"):
            text += CRLF
        return text

    def _render_menu(self, response, menu_gi):
        self.menu = []
        if self.menu_filename:
            os.unlink(self.menu_filename)
        tmpf = tempfile.NamedTemporaryFile("w", encoding="UTF-8", delete=False)
        self.menu_filename = tmpf.name
        response = io.StringIO(response)
        rendered = []
        for line in response.readlines():
            if line.startswith("3"):
                print("Error message from server:")
                print(line[1:].split("\t")[0])
                tmpf.close()
                os.unlink(self.menu_filename)
                self.menu_filename = ""
                return ""
            else:
                tmpf.write(line)

            if line.startswith("i"):
                rendered.append(line[1:].split("\t")[0] + "\n")
            else:
                try:
                    gi = gopheritem_from_line(line,
                            menu_gi.host if self.tls else None,
                            menu_gi.port if self.tls else None)
                except:
                    # Silently ignore things which are not errors, information
                    # lines or things which look like valid menu items
                    self._debug("Ignoring menu line: %s" % line)
                    continue
                if gi.itemtype == "+":
                    self._register_redundant_server(gi)
                    continue
                self.menu.append(gi)
                rendered.append(self._format_gopheritem(len(self.menu), gi) + "\n")

        self.lookup = self.menu
        self.page_index = 0
        self.menu_index = -1

        return "".join(rendered)

    def _format_gopheritem(self, index, gi, url=False):
        line = "[%d] " % index
        # Add item name, with itemtype indicator for non-text items
        if gi.name:
            line += gi.name
        # Use URL in place of name if we didn't get here from a menu
        else:
            line += gopheritem_to_url(gi)
        if gi.itemtype in _ITEMTYPE_TITLES:
            line += _ITEMTYPE_TITLES[gi.itemtype]
        elif gi.itemtype == "1" and not line.endswith("/"):
                line += "/"
        # Add URL if requested
        if gi.name and url:
            line += " (%s)" % gopheritem_to_url(gi)
        # Colourise
        if self.options["color_menus"] and gi.itemtype in _ITEMTYPE_COLORS:
            line = _ITEMTYPE_COLORS[gi.itemtype] + line + "\x1b[0m"
        return line

    def _register_redundant_server(self, gi):
        # This mirrors the last non-mirror item
        target = self.menu[-1]
        target = (target.host, target.port, target.path)
        if target not in self.mirrors:
            self.mirrors[target] = []
        self.mirrors[target].append((gi.host, gi.port, gi.path))
        self._debug("Registered redundant mirror %s" % gopheritemi_to_url(gi))

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

    def _show_lookup(self, offset=0, end=None, url=False):
        for n, gi in enumerate(self.lookup[offset:end]):
            print(self._format_gopheritem(n+offset+1, gi, url))

    def _update_history(self, gi):
        # Don't duplicate
        if self.history and self.history[self.hist_index] == gi:
            return
        self.history = self.history[0:self.hist_index+1]
        self.history.append(gi)
        self.hist_index = len(self.history) - 1

    def _log_visit(self, gi, address, size):
        self.itemtype_counts[gi.itemtype] += 1
        if not address:
            return
        self.log["requests"] += 1
        self.log["bytes_recvd"] += size
        self.visited_hosts.add(address)
        if self.tls:
            self.log["tls_requests"] += 1
        if address[0] == socket.AF_INET:
            self.log["ipv4_requests"] += 1
            self.log["ipv4_bytes_recvd"] += size
        elif address[0] == socket.AF_INET6:
            self.log["ipv6_requests"] += 1
            self.log["ipv6_bytes_recvd"] += size

    def _set_tls(self, tls):
        self.tls = tls
        if self.tls:
            self.prompt = "\x1b[38;5;196m" + "VF-1" + "\x1b[38;5;255m" + "> " + "\x1b[0m"
        else:
            self.prompt = "\x1b[38;5;202m" + "VF-1" + "\x1b[38;5;255m" + "> " + "\x1b[0m"

    def _debug(self, debug_text):
        if not self.options["debug"]:
            return
        debug_text = "\x1b[0;32m[DEBUG] " + debug_text + "\x1b[0m"
        print(debug_text)

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

        self.menu_index = n
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
            elif option == "encoding":
                try:
                    codecs.lookup(value)
                except LookupError:
                    print("Unknown encoding %s" % value)
                    return
            elif value.isnumeric():
                value = int(value)
            elif value.lower() == "false":
                value = False
            elif value.lower() == "true":
                value = True
            else:
                try:
                    value = float(value)
                except ValueError:
                    pass
            self.options[option] = value

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
                            "1", line, False)
            self._go_to_gi(gi)
        # If this isn't a mark, treat it as a URL
        else:
            url = line
            gi = url_to_gopheritem(url, self.tls)
            self._go_to_gi(gi)

    @needs_gi
    def do_reload(self, *args):
        """Reload the current URL."""
        self._go_to_gi(self.gi)

    @needs_gi
    def do_up(self, *args):
        """Go up one directory in the path."""
        new_path, removed = os.path.split(self.gi.path)
        if not removed:
            new_path, removed = os.path.split(new_path)
        new_gi = self.gi._replace(path=new_path, itemtype="1")
        self._go_to_gi(new_gi)

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
        return self.onecmd(str(self.menu_index+1))

    def do_previous(self, *args):
        """Go to previous item before current in index."""
        self.lookup = self.menu
        return self.onecmd(str(self.menu_index-1))

    @needs_gi
    def do_root(self, *args):
        """Go to root selector of the server hosting current item."""
        gi = self.gi._replace(path="", itemtype="1", name=self.gi.host)
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
            self._show_lookup()
            self.lookup = old_lookup
        elif line == "clear":
            self.waypoints = []
        elif line == "*":
            self.waypoints.extend(self.lookup)
        elif looks_like_url(line):
            self.waypoints.append(url_to_gopheritem(line, self.tls))
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

    @needs_gi
    def do_mark(self, line):
        """Mark the current item with a single letter.  This letter can then
be passed to the 'go' command to return to the current item later.
Think of it like marks in vi: 'mark a'='ma' and 'go a'=''a'."""
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
        veronica = url_to_gopheritem("gopher://gopher.floodgap.com:70/7/v2/vs")
        self._go_to_gi(veronica, query_str = line)

    ### Stuff that modifies the lookup table
    def do_ls(self, line):
        """List contents of current index.
Use 'ls -l' to see URLs."""
        self.lookup = self.menu
        self._show_lookup(url = "-l" in line)
        self.page_index = 0

    def do_history(self, *args):
        """Display history."""
        self.lookup = self.history
        self._show_lookup(url=True)
        self.page_index = 0

    def do_search(self, searchterm):
        """Search index (case insensitive)."""
        results = [
            gi for gi in self.lookup if searchterm.lower() in gi.name.lower()]
        if results:
            self.lookup = results
            self._show_lookup()
            self.page_index = 0
        else:
            print("No results found.")

    def emptyline(self):
        """Page through index ten lines at a time."""
        i = self.page_index
        if i > len(self.lookup):
            return
        self._show_lookup(offset=i, end=i+10)
        self.page_index += 10

    ### Stuff that does something to most recently viewed item
    @needs_gi
    def do_cat(self, *args):
        """Run most recently visited item through "cat" command."""
        subprocess.call(shlex.split("cat %s" % self.tmp_filename))

    @needs_gi
    def do_less(self, *args):
        """Run most recently visited item through "less" command."""
        cmd_str = self._get_handler_cmd(self.gi)
        cmd_str = cmd_str % self.tmp_filename
        subprocess.call("%s | less -R" % cmd_str, shell=True)

    @needs_gi
    def do_fold(self, *args):
        """Run most recently visited item through "fold" command."""
        cmd_str = self._get_handler_cmd(self.gi)
        cmd_str = cmd_str % self.tmp_filename
        subprocess.call("%s | fold -w 70 -s" % cmd_str, shell=True)

    @needs_gi
    def do_shell(self, line):
        """'cat' most recently visited item through a shell pipeline."""
        subprocess.call(("cat %s |" % self.tmp_filename) + line, shell=True)

    @needs_gi
    def do_save(self, line):
        """Save an item to the filesystem.
'save n filename' saves menu item n to the specified filename.
'save filename' saves the last viewed item to the specified filename.
'save n' saves menu item n to an automagic filename."""
        args = line.strip().split()

        # First things first, figure out what our arguments are
        if len(args) == 0:
            # No arguments given at all
            # Save current item, if there is one, to a file whose name is
            # inferred from the gopher path
            if not self.tmp_filename:
                print("You need to visit an item first!")
                return
            else:
                index = None
                filename = None
        elif len(args) == 1:
            # One argument given
            # If it's numeric, treat it as an index, and infer the filename
            try:
                index = int(args[0])
                filename = None
            # If it's not numeric, treat it as a filename and
            # save the current item
            except ValueError:
                index = None
                filename = os.path.expanduser(args[0])
        elif len(args) == 2:
            # Two arguments given
            # Treat first as an index and second as filename
            index, filename = args
            try:
                index = int(index)
            except ValueError:
                print("First argument is not a valid item index!")
                return
            filename = os.path.expanduser(filename)
        else:
            print("You must provide an index, a filename, or both.")
            return

        # Next, fetch the item to save, if it's not the current one.
        if index:
            last_gi = self.gi
            try:
                gi = self.lookup[index-1]
                self._go_to_gi(gi, update_hist = False, handle = False)
            except IndexError:
                print ("Index too high!")
                self.gi = last_gi
                return
        else:
            gi = self.gi

        # Derive filename from current GI's path, if one hasn't been set
        if not filename:
            if gi.itemtype == '1':
                path = gi.path
                if path in ("", "/"):
                    # Attempt to derive a nice filename from the gopher
                    # item name, falling back to the hostname if there
                    # is no item name
                    if not gi.name:
                        filename = gi.host.lower() + ".txt"
                    else:
                        filename = gi.name.lower().replace(" ","_") + ".txt"
                else:
                    # Derive a filename from the last component of the
                    # path
                    if path.endswith("/"):
                        path = path[0:-1]
                    filename = os.path.split(path)[1]
            else:
                filename = os.path.basename(gi.path)

        # Check for filename collisions and actually do the save if safe
        if os.path.exists(filename):
            print("File %s already exists!" % filename)
        else:
            # Save the "source code" of menus, not the rendered view
            src_file = self.menu_filename if self.gi.itemtype in ("1", "7") else self.tmp_filename
            shutil.copyfile(src_file, filename)
            print("Saved to %s" % filename)

        # Restore gi if necessary
        if index != None:
            self._go_to_gi(last_gi, handle=False)

    @needs_gi
    def do_url(self, *args):
        """Print URL of most recently visited item."""
        print(gopheritem_to_url(self.gi))

    @needs_gi
    def do_links(self, *args):
        """Extract URLs from most recently visited item."""
        if self.gi.itemtype not in ("0", "h"):
            print("You need to visit a text item, first")
            return
        links = []
        with open(self.tmp_filename, "r") as fp:
            for line in fp:
                words = line.strip().split()
                words_containing_urls = (w for w in words if looks_like_url(w))
                urls = (extract_url(w) for w in words_containing_urls)
                links.extend([url_to_gopheritem(u) for u in urls])
        self.lookup = links
        self._show_lookup()

    ### Bookmarking stuff
    @needs_gi
    def do_add(self, line):
        """Add the current URL to the bookmarks menu.
Bookmarks are stored in the ~/.vf1-bookmarks.txt file.
Optionally, specify the new name for the bookmark."""
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
        self._set_tls(not self.tls)
        if self.tls:
            print("Battloid mode engaged! Only accepting encrypted connections.")
        else:
            print("Battloid mode disengaged! Switching to unencrypted channels.")

    ### Help
    def do_help(self, arg):
        """ALARM! Recursion detected! ALARM! Prepare to eject!"""
        if arg == "!":
            print("! is an alias for 'shell'")
        elif arg == "?":
            print("? is an alias for 'help'")
        else:
            cmd.Cmd.do_help(self, arg)

    ### Flight recorder
    def do_blackbox(self, *args):
        """Display contents of flight recorder, showing statistics for the
current gopher browsing session."""
        lines = []
        # Compute flight time
        now = time.time()
        delta = now - self.log["start_time"]
        hours, remainder = divmod(delta, 36060)
        minutes, seconds = divmod(remainder, 60)
        # Count hosts
        ipv4_hosts = len([host for host in self.visited_hosts if host[0] == socket.AF_INET])
        ipv6_hosts = len([host for host in self.visited_hosts if host[0] == socket.AF_INET6])
        # Assemble lines
        lines.append(("Flight duration", "%02d:%02d:%02d" % (hours, minutes, seconds)))
        lines.append(("Requests sent:", self.log["requests"]))
        lines.append(("   IPv4 requests:", self.log["ipv4_requests"]))
        lines.append(("   IPv6 requests:", self.log["ipv6_requests"]))
        lines.append(("   TLS-secured requests:", self.log["tls_requests"]))
        for itemtype in "0 1 7 8 9 h g s I T".split():
            lines.append(("   Itemtype %s:" % itemtype, self.itemtype_counts[itemtype]))
        lines.append(("Bytes received:", self.log["bytes_recvd"]))
        lines.append(("   IPv4 bytes:", self.log["ipv4_bytes_recvd"]))
        lines.append(("   IPv6 bytes:", self.log["ipv6_bytes_recvd"]))
        lines.append(("Unique hosts visited:", len(self.visited_hosts)))
        lines.append(("   IPv4 hosts:", ipv4_hosts))
        lines.append(("   IPv6 hosts:", ipv6_hosts))
        lines.append(("DNS failures:", self.log["dns_failures"]))
        lines.append(("Timeouts:", self.log["timeouts"]))
        lines.append(("Refused connections:", self.log["refused_connections"]))
        lines.append(("Reset connections:", self.log["reset_connections"]))
        # Print
        for key, value in lines:
            print(key.ljust(24) + str(value).rjust(8))

    ### The end!
    def do_quit(self, *args):
        """Exit VF-1."""
        # Clean up after ourself
        if self.tmp_filename:
            os.unlink(self.tmp_filename)
        if self.menu_filename:
            os.unlink(self.menu_filename)
        print()
        print("Thank you for flying VF-1!")
        sys.exit()

    do_exit = do_quit

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
    parser.add_argument('--debug', action='store_true',
                        help='start with debugging mode enabled')
    parser.add_argument('url', metavar='URL', nargs='*',
                        help='start with this URL')
    parser.add_argument('--tls', action='store_true',
                        help='secure all communications using TLS')
    args = parser.parse_args()

    # Instantiate client
    gc = GopherClient(debug=args.debug, tls=args.tls)

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
        gc.cmdqueue.append("bookmarks")
    elif args.url:
        if len(args.url) == 1:
            gc.cmdqueue.append("go %s" % args.url[0])
        else:
            for url in args.url:
                if not url.startswith("gopher://"):
                    url = "gopher://" + url
                gc.cmdqueue.append("tour %s" % url)
            gc.cmdqueue.append("tour")

    # Endless interpret loop
    while True:
        try:
            gc.cmdloop()
        except KeyboardInterrupt:
            print("")

if __name__ == '__main__':
    main()
