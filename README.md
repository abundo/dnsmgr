# dnsmgr

Manage dns forward and reverse zonefiles, from a hostfile

Can be run both as a script, and used as a library


# Installation

Tested on Ubuntu 16.04

Clone repository

    cd /opt
    git clone https://github.com/lowinger42/dnsmgr.git


# Setup

These instructions are for running dnsmgr on same machine as primary DNS.


Create directories and adjust permissions
Assumes the primary zonefiles are stored at /etc/bind/master

    sudo mkdir /etc/dnsmgr
    chown $USER /etc/dnsmgr
    
    sudo mkdir /etc/bind/master/include
    sudo chown root:bind /etc/bind/master/include
    adduser $USER bind
    chmod g+w /etc/bind/master
    

Create hosts file

    cd /etc/dnsmgr
    
    cat >hosts
    ns1       192.168.1.1
    www       192.168.1.2
    <ctrl-d>


For each zonefile, add a INCLUDE statement that includes the generated zonefiles

    cd /etc/bind/master
    mkdir include
    
    echo "INCLUDE /etc/bind/master/include/example.com" >>example.com
    echo "INCLUDE /etc/bind/master/include/1.168.192.in-addr.arpa" >>1.168.192.in-addr.arpa


Add permissions so user can reload updated zonefiles

    visudo
    
    anders ALL=(root) NOPASSWD: /usr/sbin/rndc *
    anders ALL=(root) NOPASSWD: /usr/sbin/service bind9 restart



# Generate

Every time the hosts file is changed, just rerun the script

    cd /opt/dnsmgr
    ./dnsmgr.py rebuild --hostsfile /etc/dnsmgr/hosts
    
