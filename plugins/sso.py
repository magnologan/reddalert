from __future__ import absolute_import
import logging
import re
import requests
import urllib
from multiprocessing import Pool
from plugins.route53 import load_route53_entries, is_external


def page_redirects(location):
    try:
        if location.endswith("."):
            location = location[:-1]
        resp = requests.get(location, allow_redirects=False, timeout=3)
        code = resp.status_code
        if code == 302:
            return location, urllib.unquote(resp.headers['location'])
        else:
            return location, SSOUnprotected.UNPROTECTED
    except:
        return location, "-"


class SSOUnprotected:

    UNPROTECTED = 'unprotected'
    SSO_URL = ''
    GODAUTH_URL = ''

    def __init__(self):
        self.plugin_name = 'sso_unprotected'
        self.logger = logging.getLogger(self.plugin_name)

    def init(self, edda_client, config, status):
        self.edda_client = edda_client
        self.config = config
        self.status = status
        self._initialize_status()

    def _initialize_status(self):
        SSOUnprotected.GODAUTH_URL = self.config['godauth_url']
        SSOUnprotected.SSO_URL = self.config['sso_url']
        if 'redirects' not in self.status:
            self.status['redirects'] = []

    def run(self):
        ips = self.load_aws_ips()
        legit_domains = self.config.get("legit_domains", [])
        exempts = self.config.get("exception_domains", [])
        dns_names = self.load_known_dns()
        not_aws = {name: entry for name, entry in dns_names.iteritems()
                   if is_external(entry, ips, legit_domains) and name not in exempts}
        locations_http = ["http://%s" % name for name in not_aws.keys()]
        locations_https = ["https://%s" % name for name in not_aws.keys()]
        locations = list(locations_http + locations_https)
        self.logger.info("fetching %d urls on 16 threads" % len(locations))
        redirect_items = Pool(16).map(page_redirects, locations)
        redirects = dict(redirect_items)
        old_redirects = self.status.get("redirects", {})
        alerts = {loc: r for loc, r in redirects.iteritems()
                  if loc not in old_redirects
                  or old_redirects[loc] != r
                  }
        self.status["redirects"] = redirects
        for location, redirect in alerts.iteritems():
            loc_re = re.search('(http[s]*)://(.*)', location)
            red_re = re.search('(http[s]*)://(.*)', redirect)
            if self.SSO_URL + location == redirect:
                continue
            if red_re and loc_re.group(2) == red_re.group(2) and red_re.group(1) == 'https' and loc_re.group(1) == 'http':
                continue

            if redirect == self.UNPROTECTED:
                yield {
                    "plugin_name": self.plugin_name,
                    "id": location,
                    "details": list(["This domain (%s) is neither behind SSO nor GODAUTH" % location])
                }
            # elif redirect.startswith(self.GODAUTH_URL):
            #     yield {
            #         "plugin_name": self.plugin_name,
            #         "id": location,
            #         "details": list(["This domain (%s) is using GODAUTH" % location])
            #     }
            # else:
            #     yield {
            #         "plugin_name": self.plugin_name,
            #         "id": location,
            #         "details": list(["This domain (%s) is not reachable" % location])
            #     }

    def load_aws_ips(self):
        aws_machines = self.edda_client.soft_clean().query("/api/v2/view/instances;_expand")
        return [m.get("publicIpAddress") for m in aws_machines]

    def load_known_dns(self):
        route53_zone = self.config.get("zone")
        entries = load_route53_entries(self.edda_client, route53_zone)
        name_map = {e.get("name"): e for e in entries}
        return name_map