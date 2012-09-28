from __future__ import print_function

import client
import server

client_main = client.client_main
server_main = server.server_main

if __name__=='__main__':
    p = argparse.ArgumentParser()
    p.add_argument('-s','--server',action='store_true')
    namespace, remaining_args  = p.parse_known_args()
    if namespace.server:
        server_main(remaining_args)
    else:
        client_main(remaining_args)
