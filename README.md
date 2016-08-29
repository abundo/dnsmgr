# dnsmgr

Manage dns forward and reverse zonefiles, from a hostfile

Can be run both as a script, and used as a library

The code 
- Is developed and tested on Ubuntu 14.04
- Needs python3.x


# Installation and setup

These instructions are for running dnsmgr on same machine as primary DNS.

## Install dependencies

    sudo apt-get install git python3-pip
    sudo pip3 install orderedattrdict

## Create directories and adjust permissions

Assumes
- the primary zonefiles are stored at /etc/bind/master
- the generated include files are stored at /etc/bind/master/include
- the script will be run by user anders

    sudo mkdir /etc/dnsmgr
    sudo chown anders /etc/dnsmgr
    
    sudo mkdir /etc/bind/master/include
    sudo chown root:bind /etc/bind/master/include
    sudo adduser anders bind
    sudo chmod g+w /etc/bind/master

## Clone repository with the code

    cd /opt
    sudo mkdir dnsmgr
    sudo chown bind dnsmgr
    git clone https://github.com/lowinger42/dnsmgr.git

## Create hosts file

    cd /etc/dnsmgr
    cp /opt/dnsmgr/hosts-example.com hosts

Edit hosts file and add/update/delete entries according to your environment.

## Add permissions so user can reload updated zonefiles

    visudo
    
    %bind ALL=(root) NOPASSWD: /usr/sbin/rndc *
    %bind ALL=(root) NOPASSWD: /usr/sbin/service bind9 restart

## Check if sudo works

    sudo rndc reload

## Test setup

    cd /opt/dnsmgr
    ./dnsmgr.py rebuild --hostsfile /etc/dnsmgr/hosts
    
This will create include files in /etc/bind/master/include
Inspect and verify that they are correct  

## Use generated files

For each zonefile, add a statement that includes the generated zonefiles

    cd /etc/bind/master
    echo "$INCLUDE /etc/bind/master/include/example.com" >>example.com
    echo "$INCLUDE /etc/bind/master/include/1.168.192.in-addr.arpa" >>1.168.192.in-addr.arpa
    echo "$INCLUDE /etc/bind/master/include/0.0.0.0.1.0.0.0.8.b.d.0.1.0.0.2.ip6.arpa"  >>0.0.0.0.1.0.0.0.8.b.d.0.1.0.0.2.ip6.arpa

# Finalize setup

To simplify, put this in a shell script 

    #!/bin/sh
    cd /opt/dnsmgr
    ./dnsmgr.py rebuild --hostsfile /etc/dnsmgr/hosts

Every time the hosts file is changed, just rerun the script.

When run, dnsmgr will check all zones in bind config, and generate include files
such as

     /tmp/example.com
     /tmp/1.168.192.in-addr.arpa
     /tmp/0.0.0.0.1.0.0.0.8.b.d.0.1.0.0.2.ip6.arpa

Each file will then be compared to these files

     /etc/bind/master/include/example.com
     /etc/bind/master/include/1.168.192.in-addr.arpa
     /etc/bind/master/include/0.0.0.0.1.0.0.0.8.b.d.0.1.0.0.2.ip6.arpa

If there is a change
- the file in /tmp/ is copied to /etc/bind/master/include
- the SOA serial is incremented for the zone
- the zone is reloaded in bind
