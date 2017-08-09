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
    
    def load(self, filename=None, records=None):
        """
        Read all records from the records file
        
        filename: file to read
        records:  where to store loaded records
        
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
            
            record = util.Record(domain=self.domain, ttl=ttl, name=name, typ=typ, value=value)
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
