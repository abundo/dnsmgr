# Definition of hosts 
#
# Format on each line is 
#     <hostname> <ipaddr> [options]
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

$DOMAIN example.com

ns1                     A        192.168.1.1
ns1                     AAAA     2001:db8:1::1

www                     A        192.168.1.2
www                     AAAA     2001:db8:1::2
