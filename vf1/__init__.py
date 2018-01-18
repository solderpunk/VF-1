from . import vf1

def main():
    # eventually we can add command line parsing here
    gc = vf1.GopherClient()
    gc.cmdloop()
    
if __name__ == "__main__":
    main()
