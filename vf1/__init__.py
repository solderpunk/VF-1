import argparse
from . import vf1



def main():
    parser = argparse.ArgumentParser(description='A command line gopher client.')
    parser.add_argument('--bookmarks', action='store_true',
                        help='start with your list of bookmarks')
    parser.add_argument('--go', metavar='URL', nargs=1,
                        help='start with this URL')
    args = parser.parse_args()

    gc = vf1.GopherClient()
    if args.bookmarks:
        gc.do_bookmarks()
    elif args.go:
        gc.do_go(args.go[0])
    gc.cmdloop()
    
if __name__ == "__main__":
    main()
