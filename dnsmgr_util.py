#!/usr/bin/env python3
'''
Common utilities
'''

import os
import sys
import ipaddress
import logging
import pprint
import yaml
import importlib.machinery
import builtins
import subprocess

from orderedattrdict import AttrDict

pp = pprint.PrettyPrinter(indent=4)

allowed_chars = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_-."


def die(msg, exitcode=1):
    print(msg)
    sys.exit(exitcode)


# Setup logger
builtins.log = logging.getLogger(__name__)

def setLogLevel(level):
    m = { 
        'info': logging.INFO,
        'warning': logging.WARNING,
        'error': logging.ERROR,
        'debug': logging.DEBUG, 
         }
    if level in m:
        logging.basicConfig(level=m[level])
    else:
        die('Incorrect log level %s' % level)


def runCmd(remote=None, cmd=None, call=False):
    if remote:
        if remote.port:
            cmd = ["-p", remote.port] + cmd
        cmd = ["ssh", remote.host] + cmd
    if call:
        return subprocess.call(cmd, timeout=10)
    return subprocess.check_output(cmd, timeout=10)


class MyFile:
    """
    Represents a real file and a new temporary file
    Writing is done to the temporary file in memory.
    The temporary file can replace the real file if they are different
    """
    def __init__(self, filename):
        import tempfile
        self.filename = filename
        self.temp_file = tempfile.SpooledTemporaryFile(mode="a+b")

    def close(self):
        pass

    def write(self, s):
        self.temp_file.write(s.encode())

    def writeln(self, s):
        self.temp_file.write(s.encode())
        self.temp_file.write("\n".encode())

    def equal(self):
        """
        Returns true if filename and tempfile is equal
        """
        with open(self.filename, 'rb') as f:
            c1 = f.read()
        c2 = self.get_tempfile()
        return c1 == c2
    
    def get_tempfile(self):
        self.temp_file.seek(0)
        return self.temp_file.read()
    
    def replace(self):
        """
        If tempfile is different compared to filename, replace filename with tempfile
        Returns True if file was replaced
        """
        if self.equal():
            return False
        f = open(self.filename, "wb")
        self.temp_file.seek(0)
        f.write(self.temp_file.read())
        f.close()
        return True


class UtilException(Exception):
    pass


class AddSysPath:
    def __init__(self, path):
        self.path = path

    def __enter__(self):
        self.savedPath = sys.path.copy()
        sys.path.insert(0, self.path)

    def __exit__(self, typ, value, tb):
        sys.path = self.savedPath


def import_file(pythonFile):
    dir_name = os.path.dirname(pythonFile)
    module_name = os.path.basename(pythonFile)
    module_name = os.path.splitext(module_name)[0]
    loader = importlib.machinery.SourceFileLoader(module_name, pythonFile)
    with AddSysPath(dir_name):
        return loader.load_module()


def ordered_load(stream, Loader=yaml.Loader, object_pairs_hook=AttrDict):
    """
    Load Yaml document, replace all hashes/mappings with AttrDict
    """
    class Ordered_Loader(Loader):
        pass

    def construct_mapping(loader, node):
        loader.flatten_mapping(node)
        return object_pairs_hook(loader.construct_pairs(node))
    Ordered_Loader.add_constructor(
        yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
        construct_mapping)
    return yaml.load(stream, Ordered_Loader)


def yaml_load(filename):
    with open(filename, "r") as f:
        try:
            data = ordered_load(f, yaml.SafeLoader)
            return data
        except yaml.YAMLError as err:
            raise UtilException("Cannot load YAML file %s, err: %s" % (filename, err))


def verify_dnsname(name):
    """Check if a name contains valid characters"""
    for n in name:
        if n not in allowed_chars:
            return False
    return True


class RR:
    """
    One Resource Record
    """
    def __init__(self, domain=None, ttl="", name=None, typ=None, value=None, obj=None):
        self.domain = domain
        self.ttl = ttl
        self.name = name
        self.fqdn = "%s.%s" % (self.name, self.domain)
        self.typ = typ.upper()
        self.value = value
        self.obj = obj

    def __str__(self):
        return "domain=%s, name=%s, typ=%s, value=%s obj=%s" % \
            (self.domain, self.name, self.typ, self.value, self.obj)


class Record:
    """
    Represents one record with type and values
    """
    def __init__(self, domain=None, ttl="", name=None, typ=None, value=None, mac_address=None):
        self.domain = domain
        self.ttl = ttl
        self.name = name
        self.typ = typ
        if isinstance(value, list):
            self.value = value
        else:
            self.value = [value]
        self.fqdn = "%s.%s" % (name, domain)
        self.mac_address = mac_address      # For writing DHCP config
    
    def __str__(self):
        return "Record(domain=%s, ttl=%s, name=%s, typ=%s, value=%s mac_address=%s)" %\
            (self.domain, self.ttl, self.name, self.typ, self.value, self.mac_address)
    
    def add_value(self, value):
        if isinstance(value, list):
            self.value += value
        else:
            self.value.append(value)
        
    def value_as_str(self):
        res = []
        for value in self.value:
            res.append(str(value))
        return ", ".join(res)


class Records:
    """
    Manage a list of records
    """    
    def __init__(self):
        self._records = {}    # key is name+typ
        self.domain = None
    
    def __len__(self):
        return len(self._records)

    def _add(self, record):
        key = record.fqdn + chr(0) + record.typ
        if key in self._records:
            # Record exist, add additional value to it
            self._records[key].add_value(record.value)
        else:
            self._records[key] = record

    def __iter__(self):
        keys = list(self._records.keys())
        keys.sort()
        for key in keys:
            yield self._records[key]
            
    def items(self):
        return self._records.items()

    def get(self, name):
        return self._records[name]

    def load(self, filename=None):
        """
        Read all records from the records file
        Empty lines and comments starting with # or ; is ignored

        recursive function, to handle $INCLUDE to other files
        """
        for line in open(filename, "r"):
            line = line.rstrip()
            if line == "" or line[0] == "#" or line[0] == ";":
                continue
            if line[0] == "$":
                tmp = line.split(None, 2)
                if len(tmp) < 2:
                    raise ValueError("Invalid $ syntax: %s" % line)
                elif tmp[0] == "$DOMAIN":
                    self.domain = tmp[1]
                elif tmp[0] == "$INCLUDE":
                    self.load(filename=tmp[1])
                else:
                    raise ValueError("Invalid command %s" % tmp[0])
                continue
    
            tmp = line.split(None, 3)
            if len(tmp) < 2:
                raise ValueError("Invalid syntax: %s" % line)
            
            name = tmp.pop(0)
            if name != "@" and not verify_dnsname(name):
                raise ValueError("Invalid name: %s in %s" % (name, line))

            if tmp[0].isdigit():
                ttl = tmp.pop(0)
            else:
                ttl = ""
            typ = tmp.pop(0).upper()
            value = tmp.pop(0)
            if typ == "A":
                value = ipaddress.IPv4Address(value)
            elif typ == "AAAA":
                value = ipaddress.IPv6Address(value)
            elif typ not in ["CNAME", "MX", "NS", "PTR", "SRV", "SSHFP", "TLSA", "TSIG", "TXT"]:
                raise ValueError("Invalid type: %s in %s" % (typ, line))
            
            record = Record(domain=self.domain, ttl=ttl, name=name, typ=typ, value=value)
            self._add(record)


class Mtrie4:
    """
    Implements Longest Prefix Match, for IPv4 addresses
    Uses a mtrie with 8-8-8-8 distribution
    
    Note, there are no functionality to remove prefixes
    Note, add prefixes in correct order, start with all /32 down to /1
    """
    
    class Node:
        def __init__(self):
            self.child = {}
            self.obj = {}
        
        def __repr__(self):
            return "Node(%s, %s)" % (self.child, self.obj)
   
    def __init__(self):
        self.root = self.Node()
    
    def add_prefix(self, prefix, obj):
        p = self.root
        ip = str(prefix[0]).split(".")
        l = prefix.prefixlen

        while l > 8:
            i = int(ip.pop(0))
            if i not in p.child:
                p.child[i] = self.Node()
            p = p.child[i]
            l -= 8
        
        i = int(ip.pop(0))
        b = i
        e = i + (128 >> l)
        for ix in range(b, e + 1):
            if ix not in p.obj:
                p.obj[ix] = obj

    def lookup(self, addr):
        """
        Search using LPM
        Returns obj if found
        If not found, returns None
        """
        p = self.root
        ip = addr.split(".")
        found = None
        while True:
            i = int(ip.pop(0))
            if i in p.obj:
                found = p.obj[i]
            if i not in p.child:
                return found
            p = p.child[i]


class Mtrie6:
    """
    Implements Longest Prefix Match, for IPv6 addresses
    
    Note, there are no functionality to remove prefixes
    """
    class Node:
        def __init__(self):
            self.child = {}
            self.obj = {}
        
        def __repr__(self):
            return "Node(%s, %s)" % (self.child, self.obj)

    def __init__(self):
        self.root = self.Node()

    
    def add_prefix(self, prefix, obj):
        p = self.root
        ip = prefix.exploded.replace(":", "")
        l = prefix.prefixlen
        if l % 4 != 0:
            raise ValueError("Cannot handle IPv6 prefixes on non-nibbles")

        while l > 4:
            i = int(ip[0], 16)
            ip = ip[1:]
            if i not in p.child:
                p.child[i] = self.Node()
            p = p.child[i]
            l -= 4
        
        i = int(ip[0])
        ip = ip[1:]
        b = i
        e = i + (128 >> l)
        for ix in range(b, e + 1):
            if ix not in p.obj:
                p.obj[ix] = obj


    def lookup(self, addr):
        """
        Search using LPM
        Returns obj if found
        If not found, returns None
        """
        addr = ipaddress.IPv6Address(addr)
        p = self.root
        ip = addr.exploded.replace(":", "")
        found = None
        while True:
            i = int(ip[0], 16)
            ip = ip[1:]
            if i in p.obj:
                found = p.obj[i]
            if i not in p.child:
                return found
            p = p.child[i]
        

class Zone:
    
    def __init__(self, zone, zonefile=None, typ=None, prefix=None):
        self.zone = zone
        self.typ = typ
        self.prefix = prefix
        if zonefile:
            self.zonefile = zonefile
        else:
            self.zonefile = zone
        
        self.records = {}
        self.l = len(self.zone)

    def __str__(self):
        return "Zone(name %s, typ %s, prefix %s, zonefile %s)" % \
            (self.zone, self.typ, self.prefix, self.zonefile)
 
    def __repr__(self):
        return "Zone %s" % self.zone
 
    def __len__(self):
        return len(self.records)

    def __iter__(self):
        keys = list(self.records.keys())
        keys.sort()
        for key in keys:
            yield self.records[key]

    def add_rr(self, rr):
        key = str(rr.name) + rr.domain
        if key in self.records:
            self.records[key].append(rr)
        else:
            self.records[key] = [rr]


class Zones:
     
    def __init__(self):
        self.zones = []
        self.reverse4 = []
        self.reverse6 = []
        
        self.lpm4 = None
        self.lpm6 = None

    def __iter__(self):
        for zone in self.zones:
            yield zone
        for zone in self.reverse4:
            yield zone
        for zone in self.reverse6:
            yield zone

    def init_search(self):
        """
        Sort the IPv4 and IPv6 prefixes and create a data structure for
        fast longest prefix match
        """
        self.lpm4 = Mtrie4()
        self.lpm6 = Mtrie6()
        
        self.reverse4.sort(key=lambda x: x.prefix.prefixlen, reverse=True)
        for zone in self.reverse4:
            self.lpm4.add_prefix(zone.prefix, zone)
            
        self.reverse6.sort(key=lambda x: x.prefix.prefixlen, reverse=True)
        for zone in self.reverse6:
            self.lpm6.add_prefix(zone.prefix, zone)

    #
    # Zones
    #
     
    def add_zone(self, zone):
        """
        Forward zones
        Keep sorted on longest zonename
        """
        a = Zone(zone, typ="forward")
        tmp = self.zones + [a]
        tmp.sort(key=lambda x: len(x.zone))
        self.zones = tmp

    def add_zone_reverse4(self, zonename):
        """
        Add a IPv4 reverse zone
        input is the name of the zone, for example 1.168.192.in-addr.arpa
        The name is used to create the prefix, used for LPM
        """
        if not zonename.endswith(".in-addr.arpa"):
            raise ValueError("IPv4 reverse zone must end in .in-addr.arpa")
        
        # Extract the prefix, so we can do LPM
        prefix = zonename[:-13]
        tmp = prefix.split(".")
        if len(tmp) > 3:
            raise ValueError("Can't extract IP addresses from zonename. %s" % tmp)
        prefixlen = 8 * len(tmp)
        
        # Reverse the addresses
        prefix = []
        for t in tmp:
            prefix.insert(0, t)
        while len(prefix) != 4:
            prefix.append("0")

        prefixstr = ".".join(prefix)
        prefixstr += "/%s" % prefixlen
        prefix = ipaddress.IPv4Network(prefixstr, strict=True)
        
        zone = Zone(zone=zonename, prefix=prefix, typ="reverse4")
        self.reverse4.append(zone)

    def add_zone_reverse6(self, zonename):
        """
        Add a IPv6 reverse zone
        input is the name of the zone, for example 1.0.0.0.c.e.f.d.0.7.4.0.1.0.0.2.ip6.arpa
        The name is used to create the prefix, used for LPM
        """
        if not zonename.endswith(".ip6.arpa"):
            raise ValueError("IPv6 reverse zone must end in .ip6.arpa")

        # Extract the prefix, so we can do LPM
        prefix = zonename[:-9]
        tmp = prefix.split(".")
        if len(tmp) > 31:
            raise ValueError("Can't extract IP addresses from zonename. %s" % tmp)
        prefixlen = 4 * len(tmp)
        
        # Reverse the addresses
        prefix = []
        for t in tmp:
            prefix.insert(0, t)
        while len(prefix) != 32:
            prefix.append("0")
        
        prefixstr = ""
        for ix in range(0, len(prefix)):
            if ix and (ix % 4) == 0:
                prefixstr += ":"
            prefixstr += prefix[ix]
        prefixstr += "/%s" % prefixlen
        prefix = ipaddress.IPv6Network(prefixstr, strict=True)
        log.debug("prefix %s", prefix)
        
        zone = Zone(zone=zonename, prefix=prefix, typ="reverse6")
        self.reverse6.append(zone)


    #
    # Records
    #
    
    def add_rr(self, rr):
        # search for a matching zone
        for zone in self.zones:
            if zone.zone == rr.domain:
                zone.add_rr(rr)
                return
        log.info("Ignored, NOT handling forward DNS for %s", rr)

    def add_rr_reverse4(self, rr):
        zone = self.lpm4.lookup(str(rr.name))
        if zone is not None:
            zone.add_rr(rr)
        else:
            log.warning("Ignored, NOT handling reverse DNS for %s", rr)

    def add_rr_reverse6(self, rr):
        zone = self.lpm6.lookup(rr.name)
        if zone is not None:
            zone.add_rr(rr)
        else:
            log.warning("Ignored, NOT handling reverse DNS for %s", rr)


class BaseCLI:
    
    def __init__(self):
        import argparse
        self.parser = argparse.ArgumentParser()
        self.add_arguments2()
        self.add_arguments()
        self.args = self.parser.parse_args()
        
    def add_arguments2(self):
        """Superclass overrides this to add additional arguments"""

    def add_arguments(self):
        """Superclass overrides this to add additional arguments"""

    def run(self):
        raise ValueError("You must override the run() method")


class MyCLI:
    """
    Helper class, to construct a CLI
    """
    def __init__(self, name, **kwargs):
        # get all CLI modules
        self.cmds = AttrDict()
        current_module = sys.modules[name]
        for key in dir(current_module):
            if key.startswith("CLI_"):
                cls = getattr(current_module, key)
                self.cmds[key[4:]] = cls
    
        # get first arg, use as command
        if len(sys.argv) < 2:
            self.usage("No command specified, choose one of:")
        
        cmd = sys.argv.pop(1)
        if not cmd in self.cmds:
            self.usage("Unknown command '%s'" % cmd)
    
        obj = self.cmds[cmd](**kwargs)
        obj.run()
        

    def usage(self, msg):
        if msg: 
            print(msg)
        for cmd in self.cmds:
            print("   ", cmd)
        sys.exit(1)
