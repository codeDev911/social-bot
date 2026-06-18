import random
from lib.proxies import proxies_list
def get_proxy(): 

        username = 'auexai_DCodx'
        password = 'KxQ+1Hv~z7vtL7ml'
        proxy = random.choice(proxies_list)
        countryCode = proxy["countryCode"]
        entryPoint = proxy["entryPoint"]
        port = proxy["port"]
        proxy = f"{entryPoint}:{port}"
        proxy_url = 'https://user-%s-country-%s:%s@%s' % (username,countryCode, password, proxy)

        print(proxy_url)

if __name__=="__main__":
    get_proxy()