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

router                  192.168.1.1
router                  2001:db8:1::1
