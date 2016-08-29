#!/usr/bin/env python3
'''
Class to handle ISC bind nameserver

If hostname specified, manage a bind instance over ssh, 
otherwise manage a local bind

It is assumed SSH is configured for passwordless login.

Note:
   To update SOA serial, the serial number should be on its own line, and
   have a comment "; Serial" after it
   
   for rndc commands, make sure user has correct permissions, or allowed to sudo.
   Example in /etc/sudoers
     anders ALL=(root) NOPASSWD: /usr/sbin/rndc *
     anders ALL=(root) NOPASSWD: /usr/sbin/service bind9 restart
'''

import subprocess
import datetime
import ipaddress

from orderedattrdict import AttrDict


def ipv4_addr_to_reverse(addr):
    """
    Returns the string in reverse, dot as delemiter
    1.2.3.4 returns 4.3.2.1
    """
    ip = addr.split(".")
    return ".".join(reversed(ip))


def ipv6_addr_to_reverse(addr):
    """
    Returns the IPv6 address, expanded and a dot between each hex digit
    2001:db8::1 returns 1.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.8.b.d.0.1.0.0.2
    """
    addr = ipaddress.IPv6Address(addr)
    ip = addr.exploded.replace(":", "")
    return ".".join(reversed(ip))


class NS_Exception(Exception):
    pass


class ZoneInfo(AttrDict):
    def __init__(self):
        super().__init__()
        self.name = None        # name of zone
        self.file = None        # full path to file with resource records
        self.typ = None         # master, slave etc


def runCmd(remote=None, cmd=None, call=False):
    if remote:
        if remote.port:
            cmd = ["-p", remote.port] + cmd
        cmd = ["ssh", remote.host] + cmd
    if call:
        return subprocess.call(cmd, timeout=10)
    return subprocess.check_output(cmd, timeout=10)


class ParserException(Exception):
    pass


class Parser:
    """
    Parser, for bind configuration files
    """
    def __init__(self, f):
        self.f = f
        self.stack = ""
        self.tokenchars = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"

    def getNextChar(self):
        """Returns None at end of file"""
        if self.stack:
            c = self.stack[-1]
            self.stack = self.stack[:-1]
            return c
        c = self.f.read(1)
        if c:
            return c
        return None

    def ungetChar(self, c):
        self.stack += c
    
    def getToken(self):
        """
        Return next token
        Skip spaces
        Skip comments
        If quoted, continue to next quote and return string
        """
        token = ""
        while True:
            c = self.getNextChar()
            if c is None:
                return c
            while c and c in " \n\t":
                c = self.getNextChar()

            if c == '"':
                # string, parse to next quote
                while True:
                    c = self.getNextChar()
                    if c == '"' or c is None:
                        return token
                    token += c
                    
            if c == ';' or c == '#':
                # comment, ignore rest of line
                while c and c != '\n':
                    c = self.getNextChar()
                continue

            if c == '/':
                c2 = self.getNextChar()
                if c2 == '/':
                    # comment. ignore rest of line
                    while c2 and c2 != '\n':
                        c2 = self.getNextChar()
                    continue
                self.ungetChar(c2)
 
            if c is not None:
                token += c
            c = self.getNextChar()
            while c and c in self.tokenchars:
                token += c
                c = self.getNextChar()
            if c:
                self.ungetChar(c)
            return token
                
        # end of file, return what we have
        if token:
            return token
        return None

    def requireToken(self, req):
        """
        Get next token, make sure it matches the required token
        """
        tmp = self.getToken()
        if tmp != req:
            raise ParserException("Missing token %s" % tmp)
        return tmp


class FileMgr:
    """
    Handle reading and writing files, locally or over SSH
    """
    def __init__(self, remote=None, filename=None, mode="r"):
        self.remote = remote
        self.proc = None    # subprocess being run
        self.f = None       # file handle to read/write, for subprocess
        if filename:
            self.open(filename, mode)
        
    def open(self, filename, mode="r"):
        self.filename = filename
        self.mode = mode
        if mode not in ["r", "w"]:
            raise FileNotFoundError("Unknown file mode %s" % mode)
        
        if self.remote:
            cmd = ["ssh"]
            if self.remote.port:
                cmd.append("-p")
                cmd.append(self.remote.port)
            cmd.append(self.remote.host)
            if mode == "r":
                cmd += ["cat", filename]
                self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, bufsize=1)
                self.f = self.proc.stdout
            else:
                cmd += ["cat >%s" % filename]
                self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
                self.f = self.proc.stdin
        else:
            self.f = open(filename, mode + "b")

    def read(self, length=None):
        if self.mode == "r":
            if length:
                return self.f.read(length).decode()
            return  self.f.read().decode()
        raise FileNotFoundError("Cannot read from file opened for write")

    def readline(self):
        if self.mode == "r":
            return self.f.readline()
        raise FileNotFoundError("Cannot readline() from file opened for write")
    
    def write(self, msg):
        if self.mode == "w":
            if isinstance(msg, str):
                self.f.write(msg.decode())
            else:
                self.f.write(msg)
            return
        raise FileNotFoundError("Cannot write to file opened for read")

    def close(self):
        self.f.close()
        if self.remote:
            # todo, wait for subprocess to quit?
            pass
    
    def size(self):
        """
        Returns size of the file
        """
        cmd = ["stat", "-c", "%s", self.filename]
        out = runCmd(self.remote, cmd)
        return int(out)
    
    def copy(self, dest):
        """
        Copy the file
        """
        if not isinstance(dest, FileMgr):
            raise ValueError("dest file must be instance of FileMgr")

        if self.remote and dest.remote:
            raise ValueError("Can't copy source->dest if both are remote files, not implemented")
        
        elif self.remote:
            cmd = ["scp"]
            if self.remote.port:
                cmd += ["-P", self.remote.port]
            cmd += ["%s:%s" % (self.remote.host, self.filename), dest.filename]
            return runCmd(cmd=cmd)

        elif dest.remote:
            cmd = ["scp"]
            if dest.remote.port:
                cmd += ["-P", dest.remote.port]
            cmd += [self.filename, "%s:%s" % (dest.remote.host, dest.filename)]
            return runCmd(cmd=cmd)

        cmd = ["cp", "--force", self.filename, dest.filename]
        return runCmd(cmd=cmd)
                
            
    def move(self, dest):
        """
        Move the file
        todo, make the object invalid, or point to the new path?
        """
        if not isinstance(dest, FileMgr):
            raise ValueError("dest file must be instance of FileUtil")

        if self.remote and not dest.remote:
            raise ValueError("Not implemented, cannot move from local to remote server")
        if not self.remote and dest.remote:
            raise ValueError("Not implemented, cannot move from remote to local server")
        
        cmd = ["mv", "--force", self.filename, dest.filename]
        ret = runCmd(self.remote, cmd, call=True)
        return ret
        
    def sha256sum(self):
        """Calculate sha256 checksum on file"""
        cmd = ["sha256sum", self.filename]
        out = runCmd(self.remote, cmd)
        return out.split()[0].decode()

    def compare(self, dest):
        """
        Compare file with another file
        This is done by calculating and comparing sha256 checksum
        Returns True if files are identical
        """
        sum1 = self.sha256sum()
        sum2 = dest.sha256sum()
        return sum1 == sum2

    
class BindMgr:
    """
    Helper to manage a bind instance
    """
    
    # We always ignore these zones
    ignorezones = {
        "." : 1, 
        "localhost" : 1, 
        "127.in-addr.arpa" : 1,
        "0.in-addr.arpa" : 1,
        "255.in-addr.arpa" : 1,
    }
    
    def __init__(self, host=None, port=22):
        if host:
            self.remote = AttrDict(host=host, port=port)
        else:
            self.remote = None
        self.zones = {}

    def restart(self):
        """
        Restart the bind process
        """
        cmd = ["sudo", "service", "bind9", "restart"]
        return runCmd(self.remote, cmd)
    
    def reloadZone(self, zone=None):
        """
        reload zone content, one or all zones
        """
        cmd = ["sudo", "/usr/sbin/rndc", "reload"]
        if zone:
            cmd.append(zone)
        return runCmd(self.remote, cmd)

    def increaseSoaSerial(self, zoneinfo):
        """
        Increase serial number in a zonefile
        First verifies that the SOA has the format YYYYMMDDxx, with a valid date
        Extra check: new file with updated soa must have same size as old file
        """
        if zoneinfo.typ != "master":
            raise NS_Exception("increaseSoaSerial only makes sense for zone type master")
        
        tmpfile = "/tmp/%s" % zoneinfo.name

        # Copy file to temp
        fsrc = FileMgr(self.remote, zoneinfo.file)
        fdst = FileMgr(filename=tmpfile, mode="w")
        fsrc.copy(fdst)

        # compare checksums on original and copied file
        if not fsrc.compare(fdst):
            raise NS_Exception("Error, copied file differs in checksum")
        
        # We now have a verified copy of the file locally, Search for serial number
        f = open(tmpfile)
        fpos = f.tell()
        line = f.readline()
        serial = None
        while line:
            line = line.rstrip()
            if line.lower().endswith("; serial"):
                serial = line
                serialfpos = fpos
            fpos = f.tell()
            line = f.readline()
        f.close()
        
        if serial is None:
            raise NS_Exception("Can't find serial number in file %s" % zoneinfo.file)
        
        # search backwards for first digit
        p = len(serial) - len("; Serial")
        while not serial[p].isdigit():
            p -= 1
            if p < 0:
                raise NS_Exception("Can't find last digit in serial number in file %s" % zoneinfo.file)

        # check all 10 positions, must be digits
        p -= 9   # should be first position in serial number
        if p < 0:
            raise NS_Exception("Can't find all digist in serial number in file %s" % zoneinfo.file)
        if not serial[p: p+9].isdigit():
            raise NS_Exception("Can't find serial number in file %s" % zoneinfo.file)

        # check if serial starts with a valid date
        datestr = serial[p:p+8]
        try:
            d = datetime.datetime.strptime(datestr, "%Y%m%d")
        except ValueError as err:
            raise NS_Exception("Serial number does not start with a valid date, in file %s" % zoneinfo.file)

        # Should we go to todays date?

        s = serial[p+8:p+10]
        if s == "99":
            # todo, increase to next day and set serial=00
            raise NS_Exception("Serial number ends at 99, not implementad to handle this, in file %s" % zoneinfo.file)
        serial = datestr + str(int(s) + 1).zfill(2)

        # Ok, write the new serial to the temp file
        f = open(tmpfile, "r+b")
        f.seek(serialfpos + p)
        f.write(serial.encode())
        f.close()
        
        # Copy the file with updated serial number to server
        if self.remote:
            fsrc = FileMgr(filename=tmpfile)
            fdst = FileMgr(self.remote, tmpfile, mode="w")
            fsrc.copy(fdst)
        
            # Compare checksums on local and remote file so copy was ok
            if not fsrc.compare(fdst):
                raise NS_Exception("Error: Copy of new file failed, incorrect checksum")
            
        # Verify size between original file and file with updated serial
        # They should be identical, since serial number never changes size
        fsrc = FileMgr(remote=self.remote, filename=tmpfile)
        fdst = FileMgr(remote=self.remote, filename=zoneinfo.file)
        if fsrc.size() != fdst.size():
            raise NS_Exception("Error: Old file and new file has different sizes")
             
        # Move file to correct location
        fsrc.move(fdst)

        # Tell bind we have an updated serial no
        self.reloadZone(zoneinfo.name)
        

    def getZones(self, filename="/etc/bind/named.conf"):
        """
        Parse out all zones from bind/named configuration files
        filename is the main configuration file, it then follows
        all the includes to get all of the configuration
        """
        
        self.zones = {}      # Key is zonename, value is zoneinfo
        
        def parseZone(parser):
            zone = ZoneInfo()
            zone.name = parser.getToken()
            
            t = parser.requireToken("{")
            while t != "}":
                t = parser.getToken()
                if t == 'type':
                    zone.typ = parser.getToken()
                elif t == 'file':
                    zone.file = parser.getToken()
                    
            return zone
        
        def parseBindConfigFile(filename):
            """
            Recursive function, to handle INLINE statement
            """
            f = FileMgr(self.remote)
            f.open(filename, "r")
            parser = Parser(f)
            token = "dummy"
            while token is not None:
                token = parser.getToken()
                if token == 'include':
                    filename = parser.getToken()
                    parseBindConfigFile(filename)
                    
                elif token == 'zone':
                    zone = parseZone(parser)
                    if zone.name not in self.ignorezones:
                        self.zones[zone.name] = zone

        parseBindConfigFile(filename)
        return self.zones

    def saveZone(self, zone):
        """
        Save zone resource records
        We always write to a temp file, then comparing the new file with the
        original. If they differ we replace the old file, increase SOA serial number
        and reload the zone
        """
        zoneinfo = self.zones[zone.zonefile]
        
        savedir = "/tmp"
        filename = "%s/%s" % (savedir, zone.zonefile)
        f = open(filename, "w")
        f.write(";\n")
        f.write("; File generated by a script\n")
        f.write("; Do not edit, changes will be overwritten\n")
        f.write(";\n")
        f.write("; Zonefile : %s\n" % zone.zonefile)
        f.write("; Records  : %d\n" % len(zone))
        f.write(";\n\n")
        f.write("$ORIGIN %s.\n\n" % zone.zone)
        
        if zone.typ == "forward":
            for rrlist in zone:
                for rr in rrlist.__iter__():
                    f.write("%-30s  %-8s    %s\n" % (rr.name, rr.typ, rr.value))
            
        elif zone.typ == "reverse4":
            st = -len(zone.zone) - 1
            for rrlist in zone.__iter__():
                for rr in rrlist:
                    name = ipv4_addr_to_reverse(str(rr.name)) + ".in-addr.arpa"
                    name = name[:st]
                    f.write("%-30s  %s    %s.%s.\n" % (name, rr.typ, rr.value, rr.domain))
        
        
        elif zone.typ == "reverse6":
            st = -len(zone.zone) - 1
            for rrlist in zone.__iter__():
                for rr in rrlist:
                    name = ipv6_addr_to_reverse(rr.name) + ".ip6.arpa"
                    name = name[:st]
                    f.write("%-50s  %s    %s.%s.\n" % (name, rr.typ, rr.value, rr.domain))

        else:
            print("Error: zone %s, unknown zone type %s" % (zone.name, zone.typ))
        
        f.close()
        
        if self.remote:
            fsrc = FileMgr(filename=filename)
            fdst = FileMgr(remote=self.remote, filename=filename)
            fsrc.copy(fdst)
            
            if not fsrc.compare(fdst):
                raise NS_Exception("Error: Copied file has incorrect checksum, copy failed")

        fsrc = FileMgr(remote=self.remote, filename=filename)
        fdst = FileMgr(remote=self.remote, filename="/etc/bind/master/include/%s" % zoneinfo.name)
        
        if not fsrc.compare(fdst):
            fsrc.move(fdst)
            self.increaseSoaSerial(zoneinfo)

    
def main():
    import argparse
    
    parser = argparse.ArgumentParser()
    parser.add_argument('cmd',
                        default=None,
                        choices=[
                            "status", 
                            "restart",
                            "listzones",
                            "incsoaserial"
                        ],
                        help='Action to run',
                       )
    parser.add_argument('--host',
                        default=None,
                       )
    parser.add_argument('--port',
                        default=None,
                       )
    parser.add_argument('--zone',
                        default=None,
                       )
     
    args = parser.parse_args()
    
    bindMgr = BindMgr(host=args.host, port=args.port)
    
    if args.cmd == "status":
        print("status not implemented")
        
    elif args.cmd == "restart":
        print("Restart DNS server")
        bindMgr.restart()
        
    elif args.cmd == "listzones":
        print("List all zones")
        zones = bindMgr.getZones()
        for zone in zones.values():
            print("Name  :", zone.name)
            print("  Typ :", zone.typ)
            print("  File:", zone.file)

    elif args.cmd == "incsoaserial":
        print("Increase SOA serial for zone %s" % args.zone)
        zones = bindMgr.getZones("/etc/bind/named.conf")
        if args.zone not in zones:
            print("Nameserver does not handle zone %s" % args.zone)
        zoneinfo = zones[args.zone]
        bindMgr.increaseSoaSerial(zoneinfo)
    else:
        print("Error: unknown command %s" % args.cmd)
    
if __name__ == "__main__":
    main()
