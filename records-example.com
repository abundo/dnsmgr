# Definition of hosts 
#
# Format on each line is 
#     <hostname> <ipaddr> [options]
#
#   Valid options
#
#     mac=<mac address>       If dhcp integration is enabled, write static DHCP entry
#     reverse=<boolean>
#
# A boolean should be one of [on | off | yes | no | true | false | t | f | 1 | 0]
#
# commands:
#   $DOMAIN <domain name>
#
#     Sets the domain name, for forward DNS
#
#   $INCLUDE <filename>
#
#     Read input from <filename> 
#
#   $REVERSE <boolean>
#
#     Controls auto-generation of reverse IPv4 and IPv6 records
#     Default is to auto-generate reverse PTR records
#
#   $REVERSE4 <boolean>
#
#     Controls auto-generation of reverse IPv4 records
#
#   $REVERSE6 <boolean>
#
#     Controls auto-generation of reverse IPv6 records
#

$DOMAIN example.com

ns1                     A        192.168.1.1
ns1                     AAAA     2001:db8:1::1

www                     A        192.168.1.2
www                     AAAA     2001:db8:1::2

# Generate a DHCP static host entry
server1                 A        192.168.1.10      ; mac=12:34:12:34:12:34
