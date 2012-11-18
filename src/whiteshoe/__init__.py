from __future__ import print_function

if __name__=='__main__':
    # Will either import server
    # or import client, depending on arguments
    p = argparse.ArgumentParser()
    p.add_argument('-s','--server',action='store_true')
    namespace, remaining_args  = p.parse_known_args()

    if namespace.server:
        import server
        server.server_main(remaining_args)
    else:
        import client
        client.client_main(remaining_args)
