#!/usr/bin/env python3
'''
Classes to handle DNS, with zones

Requires a driver, for example dnsmgr_bind, that does
all the implementation specific details

Extract all network elements IP addresses
Create forward A/AAAA records
Create reverse IPv4 PTR records
Create reverse IPv6 PTR records
Create include file with zone entries
If include file has any changes, increase SOA serial number
If config ok, restart named
'''

import sys
import ipaddress
import logging

from orderedattrdict import AttrDict

import pprint
pp = pprint.PrettyPrinter(indent=4)

import dnsmgr_bind

allowed_chars = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_-."

# Setup logger
logging.basicConfig(level=logging.DEBUG)
log = logging.getLogger(__name__)
    

def verify_dnsname(name):
    """Check if a name contains valid characters"""
    for n in name:
        if not n in allowed_chars:
            return False
    return True


class RR:
    
    def __init__(self, domain=None, name=None, typ=None, value=None, obj=None):
        self.domain = domain
        self.name = name
        self.fqdn = "%s.%s" % (self.name, self.domain)
        self.typ = typ
        self.value = value
        self.obj = obj

    def __str__(self):
        return "domain=%s, name=%s, typ=%s, value=%s obj=%s" % \
            (self.domain, self.name, self.typ, self.value, self.obj)


class Host:
    """
    Represents one host, with IP addresses
    """
    def __init__(self, domain=None, name=None, addr=None):
        self.domain = domain
        self.name = name
        if type(addr) != type(list):
            self.addr = [addr]
        else:
            self.addr = addr
        self.fqdn = "%s.%s" % (name, domain)
    
    def __str__(self):
        return "Host(domain=%s, name=%s, addr=%s)" % (self.domain, self.name, self.addr)
    
    def add_ipaddr(self, addr):
        if isinstance(addr, list):
            self.addr += addr
        else:
            self.addr.append(addr)
        
    def addr_as_str(self):
        res = []
        for addr in self.addr:
            res.append(str(addr))
        return ", ".join(res)


class Hosts:
    """
    Manage a list of hosts
    """    
    def __init__(self):
        self._hosts = {}    # key is name
        self.domain = None
    
    def __len__(self):
        return len(self._hosts)

    def _add(self, host):
        key = host.fqdn
        if key in self._hosts:
            # Host exist, add additional IP address to it
            self._hosts[key].add_ipaddr(host.addr)
        else:
            self._hosts[key] = host

    def __iter__(self):
        keys = list(self._hosts.keys())
        keys.sort()
        for key in keys:
            yield self._hosts[key]
            
    def items(self):
        return self._hosts.items()

    def get(self, name):
        return self._hosts[name]

    def load(self, filename=None):
        """
        Read all hosts from the configuration file
        Empty lines and comments starting with # or ; is ignored

        recursive function, to handle $INCLUDE to other files
        """
        for line in open(filename, "r"):
            line = line.rstrip()
            if line == "" or line[0] == "#" or line[0] == ";":
                continue
            if line[0] == "$":
                tmp = line.split()
                if len(tmp) < 2:
                    raise ValueError("Invalid $ syntax: %s" % line)
                elif tmp[0] == "$DOMAIN":
                    self.domain = tmp[1]
                elif tmp[0] == "$INCLUDE":
                    self.load2(filename=tmp[1])
                else:
                    raise ValueError("Invalid command %s" % tmp[0])
                continue
    
            tmp = line.lower().split()
            if len(tmp) < 2:
                raise ValueError("Invalid syntax: %s" % line)
            
            name = tmp[0] 
            if not verify_dnsname(name):
                raise ValueError("Invalid name: %s in %s" % (tmp[0], line))
            
            addr = ipaddress.ip_address(tmp[1])
            
            host = Host(domain=self.domain, name=name, addr=addr)
            self._add(host)


class DnsException(Exception):
    pass


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
        l =  prefix.prefixlen

        while l > 8:
            i = int( ip.pop(0) )
            if i not in p.child:
                p.child[i] = self.Node()
            p = p.child[i]
            l -= 8
        
        i = int(ip.pop(0))
        if 1:
            b = i
            e = i + (128 >> l)
            for ix in range(b,e+1):
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
        l =  prefix.prefixlen
        if l % 4 != 0:
            raise ValueError("Cannot handle IPv6 prefixes on non-nibbles")

        while l > 4:
            i = int( ip[0], 16 )
            ip = ip[1:]
            if i not in p.child:
                p.child[i] = self.Node()
            p = p.child[i]
            l -= 4
        
        i = int(ip[0])
        ip = ip[1:]
        if 1:
            b = i
            e = i + (128 >> l)
            for ix in range(b,e+1):
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
        tmp = self.zones + [ a ]
        tmp.sort(key = lambda x: len(x.zone))
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
        
        tmp = Zone(zone=zonename, prefix=prefix, typ="reverse4")
        self.reverse4.append( tmp )

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
        for ix in range(0,len(prefix)):
            if ix and (ix % 4) == 0:
                prefixstr += ":"
            prefixstr += prefix[ix]
        prefixstr += "/%s" % prefixlen
        prefix = ipaddress.IPv6Network(prefixstr, strict=True)
        log.debug("prefix %s" % prefix)
        
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
        log.info("Ignored, NOT handling forward DNS for %s" % rr)

    def add_rr_reverse4(self, rr):
        zone = self.lpm4.lookup(str(rr.name))
        if zone is not None:
            zone.add_rr(rr)
        else:
            log.warning("Ignored, NOT handling reverse DNS for %s" % rr)

    def add_rr_reverse6(self, rr):
        zone = self.lpm6.lookup(rr.name)
        if zone is not None:
            zone.add_rr(rr)
        else:
            log.warning("Ignored, NOT handling reverse DNS for %s" % rr)


class DNS_Mgr:
     
    def __init__(self, driver=None):
        self.driver = driver
        self.zones = None
        self.zonesInfo = None

    def restart(self):
        self.driver.restart()

    def rebuild(self, hosts=None):
        """
        Convert all host entries to resource records, and
        update nameserver
        """
        self.zonesinfo = self.driver.getZones()
        self.zones = Zones()
        
        for name, zoneinfo in self.zonesinfo.items():
            log.debug("Adding zone %s" % name)
            if zoneinfo.typ != "master":
                continue

            if name.endswith(".in-addr.arpa"):
                self.zones.add_zone_reverse4(name)
            elif name.endswith(".ip6.arpa"):
                self.zones.add_zone_reverse6(name)
            else:
                self.zones.add_zone(name)

        self.zones.init_search()

        # Go through all hosts, and add them to the correct zone
        for host in hosts:
            domain = host.domain.lower()
            name = host.name.lower()
            
            for addr in host.addr:
                # forward
                if addr.version == 4:
                    rr = RR(domain=domain, name=name, typ="A", value=addr)
                else:
                    rr = RR(domain=domain, name=name, typ="AAAA", value=addr)
                self.zones.add_rr(rr)
         
                # reverse
                rr = RR(domain=domain, name=addr, typ="PTR", value=name)
                if addr.version == 4:
                    self.zones.add_rr_reverse4(rr)
                else:
                    self.zones.add_rr_reverse6(rr)

        # Write the files to the backend
        for zone in self.zones:
            self.driver.saveZone(zone)

    
def main():
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument('cmd',
                        default=None,
                        choices=[
                                 "status",
                                 "loadhosts",
                                 "restart",
                                 "rebuild",
                                 ],
                        help='Action to run',
                        )
    parser.add_argument('--host',
                        default=None,
                        )
    parser.add_argument('--port',
                        default=None,
                        )
    parser.add_argument('--hostsfile',
                        default=None,
                        )
    args = parser.parse_args()
    
    bindMgr = dnsmgr_bind.BindMgr(host=args.host, port=args.port)
    mgr = DNS_Mgr(driver=bindMgr)
    
    if args.cmd == "status":
        print("Status not implemented")
        
    elif args.cmd == "loadhosts":
        print("Load hosts")
        if args.hostsfile is None:
            print("Error: you need to specify hostsfile")
            sys.exit(1)
        hosts = Hosts()
        hosts.load(filename=args.hostsfile)
        for host in hosts:
            print("%s.%s" % (host.name, host.domain))
            for addr in host.addr:
                print("   ", addr)
        
    elif args.cmd == "restart":
        print("Restart DNS server")
        mgr.restart()
        
    elif args.cmd == "rebuild":
        print("Rebuild zone data from hostsfile")
        if args.hostsfile is None:
            print("Error: you need to specify hostsfile")
            sys.exit(1)
        hosts = Hosts()
        hosts.load(filename=args.hostsfile)
        mgr.rebuild(hosts=hosts)
    
    else:
        print("Error: unknown command %s" % args.cmd)
    
if __name__ == "__main__":
    main()
