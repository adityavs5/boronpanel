# WordPress management and theme interior design

## Product direction

The WordPress workspace uses one inventory as its starting point, with installation, login, management, backups and cloning attached to each site. Softaculous documents this grouped management model, including plugins, themes, maintenance, direct login, backup, restore and cloning. Boron implements its own interface and account-isolated operations rather than embedding a licensed Softaculous installation. [Softaculous WordPress Manager](https://www.softaculous.com/docs/enduser/wordpress-manager/)

The installation flow separates location, website/admin details, and a final review. Database setup is automatic. Destination folders are explicit and existing content is protected. Clones receive an independent database, updated URLs, fresh authentication salts and disabled search indexing. The original site's accounts are copied, so the interface explains that the WordPress credentials carry over. [Softaculous cloning documentation](https://www.softaculous.com/docs/enduser/clone/)

URL replacement must account for serialized WordPress data; plain SQL string replacement is not sufficient. The implementation uses WP-CLI search-replace and skips GUID columns, followed by explicit home/siteurl updates. [WordPress WP-CLI search-replace](https://developer.wordpress.org/cli/commands/search-replace/)

## Evolution interior pages

Evolution offers multiple layouts, including Icons Grid. The requested layout remains the dashboard's foundation. Interior pages use a restrained title/breadcrumb area, a consistent primary action, thin bordered sections, pale table headers and compact but readable data rows. Forms have explicit labels and readable descriptions. The treatment extends to shared components, rather than styling only WordPress or the dashboard. The hamburger drawer is removed; the home link, dashboard tool search and keyboard quick search provide navigation. [DirectAdmin Evolution documentation](https://docs.directadmin.com/directadmin/skins-and-templates/evolution.html), [DirectAdmin layout customization](https://docs.directadmin.com/changelog/version-1.642.html)

## Paper Lantern interior pages

Paper Lantern is a historical reference, rather than the current cPanel skin. Boron's version retains its slate header and compact rail, with square section borders, uppercase section/table labels, restrained backgrounds and familiar form controls. Current cPanel documentation is used only for feature vocabulary, not as evidence of historical Paper Lantern appearance. Archival screenshot references reviewed include the cPanel dashboard examples in the existing theme research and the HostHuski Paper Lantern guide. [Archival Paper Lantern reference](https://help.hosthuski.com/docs/how-to-create-a-subdomain-in-cpanel/)

## Search language

A single role-aware vocabulary serves both dashboard search and the keyboard palette. It recognizes case-insensitive words, accent normalization, prefixes and small spelling mistakes. Synonyms map user vocabulary to actual tools: “zone editor”, “DNS records” and “nameservers” map to DNS Management; “MySQL” to databases; “Softaculous” and “WP” to WordPress Manager. Results retain real labels and real routes. This is deterministic local matching, not a claim of general AI semantic search. cPanel's DNS tool is explicitly called Zone Editor, which explains the expected alternative vocabulary. [cPanel Zone Editor](https://docs.cpanel.net/cpanel/domains/zone-editor/)

## Verification criteria

Exercise both roles and both skins on desktop and phones. Verify that search finds “dns zone edito” and mixed-case terms, excludes inaccessible admin tools, and does not invent results for unrelated text. Test the installer wizard, overwrite protection, real site discovery, login token expiration/replay, plugin/theme actions, backup creation, restore and clone. Check that website PHP and management workers execute as the hosting account. Test live WordPress operations on a dedicated development account before deployment is considered complete.
