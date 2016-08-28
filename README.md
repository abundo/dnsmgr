# dnsmgr

Manage dns forward and reverse zonefiles, from a hostfile

Can be run both as a script, and used as a library

Needs python3, tested on python3.4


# Installation and setup

These instructions are for running dnsmgr on same machine as primary DNS.

The code is developed and tested on Ubuntu 14.04

Install dependencies

    sudo apt-get install python3-pip

    sudo pip3 install orderedattrdict


Create directories and adjust permissions
Assumes the primary zonefiles are stored at /etc/bind/master

    sudo mkdir /etc/dnsmgr
    sudo chown $USER /etc/dnsmgr
    
    sudo mkdir /etc/bind/master/include
    sudo chown root:bind /etc/bind/master/include
    sudo adduser $USER bind
    sudo chmod g+w /etc/bind/master


Clone repository

    sudo chown anders /opt
    cd /opt
    git clone https://github.com/lowinger42/dnsmgr.git


Create hosts file

    cd /etc/dnsmgr
    cp /opt/dnsmgr/hosts-example.com hosts

Edit hosts file if needed


For each zonefile, add a INCLUDE that includes the generated zonefiles

    cd /etc/bind/master
    mkdir include
    
    echo "INCLUDE /etc/bind/master/include/example.com" >>example.com
    echo "INCLUDE /etc/bind/master/include/1.168.192.in-addr.arpa" >>1.168.192.in-addr.arpa


Add permissions so user can reload updated zonefiles

    visudo
    
    anders ALL=(root) NOPASSWD: /usr/sbin/rndc *
    anders ALL=(root) NOPASSWD: /usr/sbin/service bind9 restart


Check if sudo works

    sudo rndc reload



# Generate

First time and every time the hosts file is changed, just rerun the script
Put this in a shell script 

    cd /opt/dnsmgr
    ./dnsmgr.py rebuild --hostsfile /etc/dnsmgr/hosts
    
This will automatically generate

     /etc/bind/master/include/example.com
     /etc/bind/master/include/1.168.192.in-addr.arpa
     
and if any of these zones are updated, the SOA is incremented and the
zone is reloaded.
