# Evolution Icons Grid and Paper Lantern design study

## Selected references

Boron will offer two independently selectable interface themes: **Evolution**, using the **Icons Grid** layout, and **Paper Lantern**, using the familiar **Basic** layout. The Evolution layout choice is explicit; Refreshed, Standard, and Sidebar are different layouts and are not the target. Theme choice changes composition, typography, navigation, tool presentation, and surfaces. Light/dark appearance remains a separate setting.

DirectAdmin documents Evolution as its default skin and describes separate component and layout color systems, customizable navigation and icons, and light/dark modes.[1] Its layout history distinguishes the Icons Grid layout from the now-removed Refreshed menu grid mode.[2] Paper Lantern was introduced in 2014 on a Bootstrap 3 foundation, emphasizing reusable, consistent interfaces.[3] It was deprecated in cPanel 100 and removed in 108, so current Jupiter documentation is not a reliable visual reference for Paper Lantern.[4]

## Visual evidence

The following images were located and visually inspected. Source screenshots are reference material, not runtime assets. Boron will retain its own name/logo and render its own icons/components.

| Reference | What it establishes | Limitations |
|---|---|---|
| [Evolution Icons Grid, DirectAdmin forum](https://forum.directadmin.com/threads/icons-grid-layout.58075/) and [full admin screenshot](https://www.woktron.com/assets/directadmin_evolution_grid.jpg) | Light full-width top bar; global search and access level; grouped outlined square tool tiles; colorful icons above labels; separate right-hand Admin Stats/widgets area | Original rollout-era screenshot, not evidence that every pixel is unchanged in 2026 |
| [DirectAdmin current Evolution documentation](https://docs.directadmin.com/directadmin/skins-and-templates/evolution.html) | Icons, layout colors, component colors, per-user skins, mobile and dark/light support | Documentation describes several layouts, not just Icons Grid |
| [Paper Lantern Basic screenshot](https://support.nitygity.com/article-images/324/pl_retro_6.png) | Dark slate top header; thin left rail; search above tool groups; slate uppercase group headers; colorful icons next to blue labels in three columns; General Information and Statistics on right | Hosting-provider branding and extra plugins are not part of Boron |
| [cPanel branding guide](https://www.cpanel.net/blog/tips-and-tricks/cpanel-branding-basics-a-guide-for-reseller-hosts/) | Paper Lantern supports provider branding and distinct styles; layout/behavior customization is part of the original design | Originally 2015, updated 2017; some old image URLs now redirect to the blog |
| [cPanel Light/Dark style article](https://www.cpanel.net/blog/products/free-cp-starter-style/) | Multiple styles and light/dark variants belong to the Paper Lantern era | Starter Light/Dark is not identical to Basic; use as appearance support evidence only |

Local reference images are saved under `/root/boron-setup/theme-research/`. The older Evolution grid reference is the visual target requested. Official Refreshed screenshots were also collected to distinguish layouts, but are not the implementation target.

## Evolution Icons Grid anatomy

The reference uses a low-contrast, white application frame. Branding is on the left of the top bar, search in the middle, and access-level/account controls to the right. The main workspace is divided into a larger tools region and a narrower statistics region. Tool groups read as sections separated by subtle rules. Individual tools are compact rectangular tiles: illustrated icons sit above centered labels, with a fine border around each tile. This produces a different rhythm from Paper Lantern's horizontal icon-label links.

Admin groups include Account Manager, Server Manager, Admin Tools, System Info & Files, and Extra Features. Customer groups map to Account Manager, E-mail Manager, Advanced Features, System Info & Files, and Extra Features. The exact DirectAdmin inventory cannot be transplanted to Boron: reseller operations and unsupported tools must not be presented as working features.

The right column emphasizes account/server facts and resource usage. Values use small labels, aligned numbers, and horizontal progress indicators. Widgets are visually subordinate to tools. For Boron, live server CPU, memory, disk, and actual account count will serve administrators; customers receive their existing account/domain/usage data. Unavailable values should be shown as unavailable, not invented.

## Paper Lantern Basic anatomy

The Basic reference uses dark slate chrome, a compact brand bar, and a narrow left navigation rail. White tool groups sit on a pale gray workspace. Every group has a strong slate heading strip with uppercase text and a collapse control at the far right. Underneath, tools are laid out in columns, with relatively large colorful pictograms beside blue text links. There are no individual bordered tile cards around each tool.

The home page begins with a wide function filter. The right side contains stacked General Information and Statistics panels. General Information presents labeled facts such as current user, primary domain, home directory, and theme. Statistics appear as divided rows with readable values and progress indicators where a real limit exists. Theme selection belongs naturally in the account information area as well as the global header.

The familiar category order for customers begins with Files, Databases, Domains, Email, Metrics, Security, Software, Advanced, and Preferences. Boron's existing features can be reorganized into these groups without changing backend endpoints. Admin functionality will use the same visual vocabulary while retaining accurate server-management names; this is not an attempt to disguise WHM as a cPanel user account.

## Shared interaction requirements

Theme switching should be immediate and should preserve the current route, form state, identity, and session. The saved theme is a browser preference, consistent with Boron's current light/dark storage. A separate persisted skin identifier avoids interpreting the existing `theme: light|dark` value as a product layout. First-paint initialization must apply the selected skin and color mode before React loads, while tolerating malformed or inaccessible local storage.

Both layouts need a complete home tool directory, a working filter, collapse/expand controls, keyboard focus visibility, and access to all existing routes. Navigation derives from the same role-aware inventory used by the current application. No control should claim unsupported functionality. Customer tools never expose admin destinations. Theme controls must be usable on mobile and available from both the header and a dedicated Appearance page.

Small screens should stack the statistics region below tools, reduce the tool grid column count, and use a drawer for complete navigation. The dark header and group strips in Paper Lantern remain recognizable at narrow widths. Evolution keeps its light-blue accents and vertical tiles. At 320px width neither shell may introduce horizontal page scrolling. Data tables retain their own overflow containers.

Detail pages must also inherit the chosen visual system: inputs, buttons, cards, dialogs, dropdowns, tables, page headings, and focus rings. The existing React components and protected routes remain the source of behavior. Theme switching must not remount the active route, or unsaved work could be lost.

## Implementation decisions and departures

Boron will use locally bundled vector icons from its existing Lucide collection with original two-color treatments, rather than extracting proprietary product assets. The icon silhouettes and layout will reflect the relevant categories. Evolution will be the default for fresh browser preferences, with Paper Lantern one click away. Existing light/dark preferences will survive migration.

The requested themes cover both administrative and customer interfaces. A new admin tool dashboard is necessary because Boron currently lands administrators directly on Accounts. Existing Accounts remains a working tool route. Customer onboarding and usage alerts will remain available on the redesigned customer dashboard.

Boron-specific adaptations include its actual security/update tools, its two-role model, its current routes, and honest empty states on this fresh server. Neither unavailable reseller controls nor fabricated customer statistics will be copied from screenshots. Dark versions are Boron adaptations of each selected visual style rather than claims of pixel-identical historical captures.

## Sources

1. DirectAdmin, [Evolution skin](https://docs.directadmin.com/directadmin/skins-and-templates/evolution.html), current documentation, accessed 2026-09-13.
2. DirectAdmin staff and community, [Icons Grid layout](https://forum.directadmin.com/threads/icons-grid-layout.58075/), 2019; [1.647 layout discussion](https://forum.directadmin.com/threads/directadmin-1-647-rc.67929/page-2), 2023. Used to distinguish Icons Grid from Refreshed grid mode.
3. cPanel, [Meet Paper Lantern](https://www.cpanel.net/blog/products/meet-paper-lantern/), 2014-02-11.
4. cPanel Support, [Paper Lantern Deprecation and Removal Schedule](https://support.cpanel.net/hc/en-us/articles/6196316404119-Paper-Lantern-Deprecation-and-Removal-Schedule), updated 2025-06-12.
5. cPanel, [Branding Basics](https://www.cpanel.net/blog/tips-and-tricks/cpanel-branding-basics-a-guide-for-reseller-hosts/), originally 2015, updated 2017-12-12.
6. cPanel, [Free cP Starter Style](https://www.cpanel.net/blog/products/free-cp-starter-style/), 2016-06-28.
7. Woktron, [Evolution admin Icons Grid screenshot](https://www.woktron.com/assets/directadmin_evolution_grid.jpg), archival visual reference.
8. NityGity, [Change cPanel Style](https://support.nitygity.com/how-to-change-cpanel-style-324), [Basic interface screenshot](https://support.nitygity.com/article-images/324/pl_retro_6.png), archival visual reference.
9. DirectAdmin, [1.660 Refreshed design update](https://docs.directadmin.com/changelog/version-1.660.html), 2024-03-01; [1.672 menu icons update](https://docs.directadmin.com/changelog/version-1.672.html), 2024-12-10. Used for disambiguation, not as the requested grid layout.
