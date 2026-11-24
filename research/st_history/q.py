import requests, json, sys, time
S = requests.Session()
S.headers.update({"User-Agent": "Mozilla/5.0", "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                  "Referer": "https://www.cninfo.com.cn/new/fulltextSearch", "X-Requested-With": "XMLHttpRequest"})
URL = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
def query(key, se, page=1, size=30, tries=4):
    data = dict(pageNum=page, pageSize=size, column="szse", tabName="fulltext", plate="", stock="", searchkey=key,
                secid="", category="", trade="", seDate=se, sortName="", sortType="", isHLtitle="true")
    for i in range(tries):
        try:
            r = S.post(URL, data=data, timeout=40); r.raise_for_status(); return r.json()
        except Exception as e:
            err = e; time.sleep(2 * (i + 1))
    raise err

def query_stock(code, org, se, page=1, key=""):
    data = dict(pageNum=page, pageSize=30, column="szse", tabName="fulltext", plate="", stock=f"{code},{org}", searchkey=key,
                secid="", category="", trade="", seDate=se, sortName="", sortType="", isHLtitle="true")
    for i in range(4):
        try:
            r = S.post(URL, data=data, timeout=40); r.raise_for_status(); return r.json()
        except Exception as e:
            err = e; time.sleep(2 * (i + 1))
    raise err
