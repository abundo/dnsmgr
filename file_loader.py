#!/usr/bin/env python3
'''
Load resource records from a text file.
'''

import os
import sys
import ipaddress

import dnsmgr_util as util

allowed_chars = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_-."


class Loader:
    """
    Load records from a file
    """

    def __init__(self):
        self.reverse4 = True
        self.reverse6 = True

    def _get_boolean(self, value):
        value = value.lower()
        if value.lower() not in ['on', 'off', 'true', 'false', '1', '0', 't', 'f', 'yes', 'no']:
            raise ValueError('Invalid value %s, should be ON or OFF' % tmp[1])
        return value in ['on', 'true', '1', 't', 'yes']
        
    def load(self, filename=None, records=None):
        """
        Read all records from the records file
        
        filename: file to read
        records:  where to store loaded records
        
        Empty lines and comments starting with # or ; is ignored

        recursive function, to handle $INCLUDE to other files
        """
        self.domain = os.path.basename(filename)
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

                elif tmp[0] == "$REVERSE":
                    self.reverse4 = self._get_boolean(tmp[1])
                    self.reverse6 = self.reverse4

                elif tmp[0] == "$REVERSE4":
                    self.reverse4 = self._get_boolean(tmp[1])

                elif tmp[0] == "$REVERSE6":
                    self.reverse6 = self._get_boolean(tmp[1])

                else:
                    raise ValueError("Invalid command %s" % tmp[0])
                continue

            mac_address = None
            reverse = None
    
            # Try to find options, end of line, starting with a ;
            p = line.rfind(';')
            if p >= 0:
                line, tmp, options = line.rpartition(';')
                for opt in options.split(' '):
                    key,tmp,val=opt.partition("=")
                    if key == 'mac':
                        mac_address = val
                    elif key == 'reverse':
                        reverse = self._get_boolean(val)
    
            tmp = line.split(None, 3)
            if len(tmp) < 2:
                raise ValueError("Invalid syntax: %s" % line)
            
            name = tmp.pop(0)
            if name != "@" and not util.verify_dnsname(name):
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
            
            if reverse is None:
                if typ == 'A':
                    reverse = self.reverse4
                elif typ == 'AAAA':
                    reverse = self.reverse6
                    
            record = util.Record(domain=self.domain, 
                                 ttl=ttl, 
                                 name=name, 
                                 typ=typ, 
                                 value=value, 
                                 mac_address=mac_address,
                                 reverse=reverse,
                                 )
            records.add(record)


def main():
    """
    Function test
    """
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument('--file',
                        default='/etc/dnsmgr/records',
                       )
    args = parser.parse_args()

    if not os.path.isfile(args.file):
        util.die("Cannot find file %s" % args.file)

    records = util.Records()
    records.load(args.file)
    
    for record in records:
        print(record)
    
    
if __name__ == "__main__":
    main()
