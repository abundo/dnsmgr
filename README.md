# DNSMGR

Manage dns forward and reverse zonefiles, from one or multiple record files

DNSMGR can be run as a standalone application, or used as a library from other
applications.

DNSMGR can run directly on the server where the nameserver is running on,
or on another computer using SSH to manage the nameserver and its configuration.

DNSMGR can generate
- Partial zonefiles
  - Use when zone file content is a mix of manual and DNSMGR created entries
  - Partial zonefiles needs to be included from the zone file
  - More flexible, but more work to setup
- Full zonefiles (TODO, not implemented yet see issue #16)
  - Use when all zone file content will be generated from the zonefile
  - Simple, all of zone file is automatically generated including SOA and NS records

The code 
- Is developed and tested on Ubuntu 14.04 and Fedora 24
- Uses python3.4 or later
  - if the nameserver is using an older python version, run DNSMGR on
    another machine and modifying the nameserver over SSH


# Installation and setup

These instructions are for running DNSMGR on the primary nameserver.

Note! Installation of nameserver software is not described here, see
the nameserver documentation (isc-bind etc)

Use of a configuration file is not needed but recommended to simplify usage.

Assumption, Ubuntu
- the primary zonefiles are stored at /etc/bind/primary
- the generated include files are stored at /etc/bind/primary/include
- the script will be run by user anders

Assumption, Fedora
- the primary zonefiles are stored at /etc/named
- the generated include files are stored at /etc/named/include
- the script will be run by user anders


## Install dependencies


### Ubuntu

    sudo apt-get install git python3-pip python3-yaml
    sudo pip3 install orderedattrdict

### Fedora

    sudo dnf install git python3-PyYAML
    sudo pip3 install orderedattrdict


## Create directories and adjust permissions

    sudo mkdir /etc/dnsmgr
    sudo chown anders /etc/dnsmgr

### Ubuntu
    
    sudo usermod -G bind anders
    sudo mkdir -p /etc/bind/primary/include
    sudo chown root:bind /etc/bind/primary/include
    sudo chmod g+w /etc/bind/primary

### Fedora

    sudo usermod -G named anders
    sudo mkdir -p /etc/named/include
    sudo chown root:named /etc/named/include
    sudo chmod g+w /etc/named/primary

## Clone repository with the code

    cd /opt
    sudo mkdir DNSMGR
    sudo chown bind DNSMGR
    git clone https://github.com/lowinger42/DNSMGR.git


## Create configuration and zones file

    cd /etc/dnsmgr
    cp /opt/dnsmgr/records-example.com records
    cp /opt/dnsmgr/dnsmgr-example.conf dnsmgr.conf
    

Edit file with records and add/update/delete entries according to your environment.

Verify that the content of the configuration file is ok.


## Add permissions so user can reload updated zonefiles

    visudo
    
    %bind ALL=(root) NOPASSWD: /usr/sbin/rndc *
    %bind ALL=(root) NOPASSWD: /usr/sbin/service bind9 restart


## Check if sudo works

    sudo rndc reload

## Test setup

    cd /opt/dnsmgr
    ./dnsmgr.py rebuild
    
This will create include files in /etc/bind/primary/include

Inspect and verify that they are correct before using them in production.


## Use generated files

For each zonefile, add a statement that includes the generated zonefiles

    cd /etc/bind/primary
    echo "$INCLUDE /etc/bind/primary/include/example.com" >>example.com
    echo "$INCLUDE /etc/bind/primary/include/1.168.192.in-addr.arpa" >>1.168.192.in-addr.arpa
    echo "$INCLUDE /etc/bind/primary/include/0.0.0.0.1.0.0.0.8.b.d.0.1.0.0.2.ip6.arpa"  >>0.0.0.0.1.0.0.0.8.b.d.0.1.0.0.2.ip6.arpa


# Finalize setup

To simplify, put this in a shell script in for example /usr/bin/update-dns

    #!/bin/sh
    cd /opt/dnsmgr
    ./DNSMGR.py rebuild

Every time the zones file is changed, just rerun the update-dns script.

When run, DNSMGR will check all zones in bind config, and generate include files
such as

     /tmp/example.com
     /tmp/1.168.192.in-addr.arpa
     /tmp/0.0.0.0.1.0.0.0.8.b.d.0.1.0.0.2.ip6.arpa

Each file will then be compared to these files

     /etc/bind/primary/include/example.com
     /etc/bind/primary/include/1.168.192.in-addr.arpa
     /etc/bind/primary/include/0.0.0.0.1.0.0.0.8.b.d.0.1.0.0.2.ip6.arpa

If there is a change
- The file in /tmp/ is copied to /etc/bind/primary/include
- The SOA serial is incremented for the zone
- The zone is reloaded in bind


# Summary of configuration files

This shows an example setup, in a Ubuntu 14.04 system


## DNSMGR zones file /etc/dnsmgr/zones

content

    $DOMAIN example.com

    ns1                     192.168.1.1
    ns1                     2001:db8:1::1
    
    www                     192.168.1.2
    www                     2001:db8:1::2


## bind zone definitions /etc/bind/named.conf.local

content


    zone "example.com" {
         type master;
         file "/etc/bind/primary/example.com";
    };
    zone "1.168.192.in-addr.arpa" {
         type master;
         file "/etc/bind/primary/1.168.192.in-addr.arpa";
    };
    zone "0.0.0.0.1.0.0.0.8.b.d.0.1.0.0.2.ip6.arpa" {
         type master;
         file "/etc/bind/primary/0.0.0.0.1.0.0.0.8.b.d.0.1.0.0.2.ip6.arpa";
    };


# bind zone files

Note the format of the serial number (YYYYMMDDxx), and the comment after. If the
serial is not in this format (needs to be a valid date), with the comment after,
the automatic increment of the serial number will not work.

### /etc/bind/primary/example.com

content


    $TTL 900
    @         SOA     ns1.example.com. support.example.com. (
                              2016082231 ; serial
                                   36000 ; refresh
                                    3600 ; retry
                                  604800 ; expire
                                     900 ; minimum
                      )
    
    @                       NS      ns1.example.com.
    
    $INCLUDE /etc/bind/primary/include/example.com
    
    # Add additional entries below, which are unmanaged by DNSMGR
    


### /etc/bind/primary/1.168.192.in-addr.arpa

content


    $TTL 900
    @         SOA     ns1.example.com. support.example.com. (
                              2016082231 ; serial
                                   36000 ; refresh
                                    3600 ; retry
                                  604800 ; expire
                                     900 ; minimum
                      )
    
    @                       NS      ns1.example.com.
    
    $INCLUDE /etc/bind/primary/include/1.168.192.in-addr.arpa
    
    # Add additional entries below, which are unmanaged by DNSMGR
    


### /etc/bind/primary/0.0.0.0.1.0.0.0.8.b.d.0.1.0.0.2.ip6.arpa

content


    $TTL 900
    @         SOA     ns1.example.com. support.example.com. (
                              2016082231 ; serial
                                   36000 ; refresh
                                    3600 ; retry
                                  604800 ; expire
                                     900 ; minimum
                      )
    
    @                       NS      ns1.example.com.

    $INCLUDE /etc/bind/primary/include/
    
    # Add additional entries below, which are unmanaged by DNSMGR
    

# Files generated by DNSMGR


### /etc/bind/primary/include/example.com

content


    ; File generated by DNSMGR
    ; Do not edit, changes will be overwritten
    ;
    ; Zonefile : example.com
    ; Records  : 2
    
    $ORIGIN example.com.
    
    ns1                                A       192.168.1.1
    www                                A       192.168.1.2


### /etc/bind/primary/include/1.168.192.in-addr.arpa

content


    ; File generated by DNSMGR
    ; Do not edit, changes will be overwritten
    ;
    ; Zonefile : 25.25.172.in-addr.arpa
    ; Records  : 2
    
    $ORIGIN 25.25.172.in-addr.arpa.
    
    1                                  PTR     ns1.example.com.      
    2                                  PTR     www.example.com.      


### /etc/bind/primary/include/0.0.0.0.1.0.0.0.8.b.d.0.1.0.0.2.ip6.arpa

content


    ; File generated by DNSMGR
    ; Do not edit, changes will be overwritten
    ;
    ; Zonefile : 0.0.0.0.1.0.0.0.8.b.d.0.1.0.0.2.ip6.arpa.
    ; Records  : 2
    
    $ORIGIN 0.0.0.0.1.0.0.0.8.b.d.0.1.0.0.2.ip6.arpa.
    
    1.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0    PTR     ns1.example.com.
    2.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0    PTR     www.example.com.

