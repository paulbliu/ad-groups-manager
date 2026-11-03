# ad-groups-manager
Monitoring AD Groups

## Usage
`pip install ldap3`, then:

    AD_PASSWORD=... python ad_groups_monitor.py --server dc.example.com \
      --user 'EXAMPLE\svc' --base-dn 'DC=example,DC=com' --group "Domain Admins"

The first run saves a snapshot; later runs print added (`+`) / removed (`-`) members and exit with 1 if anything changed.
