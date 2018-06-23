#!/usr/bin/env python3
'''
A standalone HTTP server, implementing a REST API

'''

import os
import base64
import ipaddress
import json
import http.server

from orderedattrdict import AttrDict
import dnsmgr_util as util

from dnsmgr import DNS_Mgr

auth_handler = None
config = None
mgr = DNS_Mgr()


class Auth:
    """
    Handle HTTP requests, optionally filter on client IP address 
    """
    def __init__(self, valid_prefixes=None):
        self.valid_prefixes = None
        if valid_prefixes:
            self.valid_prefixes = []
            for prefix in valid_prefixes:
                self.valid_prefixes.append(ipaddress.ip_network(prefix))
    
    def auth(self, request):
        if self.valid_prefixes is None:
            return False
        
        # Check if the request is from an allowed prefix
        addr = ipaddress.ip_network(request.client_address[0])
        for prefix in self.valid_prefixes:
            if prefix.overlaps(addr):
                return False
        
        # Don't return any response, just closing connection
        return True


class Basic_Auth(Auth):
    """
    Handle HTTP request, with basic HTTP auth
    """

    def __init__(self, username=None, password=None, valid_prefixes=None):
        super().__init__(valid_prefixes=valid_prefixes)
        self.username = username
        self.password = password
        self.key = base64.b64encode(bytes('%s:%s' % (username, password), 'utf-8')).decode('ascii')
    
    def send_authhead(self, request):
        request.send_response(401)
        request.send_header('WWW-Authenticate', 'Basic realm="dnsmgr"')
        request.send_header('Content-type', 'application/json')
        request.end_headers()

    def auth(self, request):
        if super().auth(request):
            return True
        auth_header = request.headers.get('Authorization') 
        if  auth_header == None:
            self.send_authhead(request)
            response = { 'errno' : 1, 'errmsg': 'No auth header received'}
            request.wfile.write(json.dumps(response).encode())
            return True
        if auth_header == 'Basic ' + self.key:
            return False    # Auth is correct
        
        self.send_authhead(request)
        response = { 'errno' : 2, 'errmsg': 'Invalid credentials'}
        request.wfile.write(json.dumps(response).encode())
        return True
        

class Dnsmgr_RequestHandler(http.server.BaseHTTPRequestHandler):
    """
    Handle HTTP request, implementing the API
    """

    def do_GET(self):
        if auth_handler.auth(self):
            return

        response_code = 200
        p = self.path
        if p == "/get_zones":
            data = mgr.getZones()
            message = { 'errno': 0, 'errmsg': '', 'data': data }
            
        elif p == "/restart":
            mgr.restart()
            message = { 'errno': 0, 'errmsg': '' }

        elif p == "/status":
            message = { 'errno': 1, 'errmsg': 'Not implemented' }
        
        elif p == "/update_dns":
            mgr.load()
            mgr.update_dns()
            message = { 'errno': 0, 'errmsg': '' }
        
        elif p == "/update_dhcp":
            mgr.load()
            mgr.update_dhcp()
            message = { 'errno': 0, 'errmsg': '' }
        
        elif p == "/update":
            mgr.load()
            mgr.update_dns()
            mgr.update_dhcp()
            message = { 'errno': 0, 'errmsg': '' }

        else:
            response_code = 405
            message = { 'errno': 1, 'errmsg': 'unknown API call %s' % p }
            
        self.send_response(response_code)
        self.send_header('Content-type', 'application/json')
        self.end_headers()
        message = json.dumps(message)

        self.wfile.write(bytes(message, "utf8"))


    def do_POST(self):
        if auth_handler.auth(self):
            return

        response_code = 200
        p = self.path
        
        if p.startswith("/records/"):
            f = p[9:]
            if '.' in f or '/' in f:
                # We don't accept stuff that may take us to another directory
                message = { 'errno': 1, 'errmsg': 'Invalid characters in target filename' }
            else:
                # Store new records. We only allow storage to predefined targets
                # in the configuration file, and only in basedir
                found = False
                for target in mgr.config.records:
                    if target.type == "file_loader.py":
                        if os.path.basename(target.name) == f:
                            found = True
                            break
                if found:
                    print("Writing new records to %s" % target.name)
                    content_len = int(self.headers['content-length'])
                    data = self.rfile.read(content_len).decode()
                    with open(target.name, "w") as outfile:
                        outfile.write(data)
                        outfile.write('\n')
                    message = { 'errno': 0, 'errmsg': '' }
                else:
                    message = { 'errno': 1, 'errmsg': 'No matching records file %s' % f}

        else:
            response_code = 405
            message = { 'error': 'unknown API call %s' % p }

        self.send_response(response_code)
        self.send_header('Content-type', 'application/json')
        self.end_headers()
        message = json.dumps(message)

        self.wfile.write(bytes(message, "utf8"))

        
def main():
    global auth_handler, config
    
    if not 'api' in mgr.config:
        util.die("Error: no API configuration in configuration file")
    config = mgr.config.api
    if not config.enabled:
        util.die("Error: API is not enabled")
        
    if config.auth == 'none':
        auth_handler = Auth(valid_prefixes=config.valid_prefixes)

    elif config.auth == 'basic':
        auth_handler = Basic_Auth(username=config.username,
                                  password=config.password,
                                  valid_prefixes=config.valid_prefixes)
    else:
        util.die("Error: Unknown auth type %s" % config.auth)

    server_address = (config.address, config.port)
    httpd = http.server.HTTPServer(server_address, Dnsmgr_RequestHandler)

    print("Starting server")
    httpd.serve_forever()
    

if __name__ == "__main__":
    main()
