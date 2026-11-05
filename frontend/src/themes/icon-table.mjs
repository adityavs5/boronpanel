// Every lucide icon the panel uses, mapped to its counterpart in the Classic
// (Phosphor duotone) and Grid (Remix) icon sets. Modern keeps lucide itself.
// scripts/gen-theme-icons.mjs turns this into static imports, so only icons
// the panel really uses reach the bundles, and it fails on a missing mapping.
export const ICONS = {
  Activity: ['Pulse', 'RiPulseFill'], AlertCircle: ['WarningCircle', 'RiErrorWarningFill'], AlertOctagon: ['WarningOctagon', 'RiSpam2Fill'],
  AlertTriangle: ['Warning', 'RiAlertFill'], AppWindow: ['AppWindow', 'RiWindow2Fill'], Archive: ['Archive', 'RiArchive2Fill'],
  ArchiveRestore: ['ArrowCounterClockwise', 'RiInboxUnarchiveFill'], ArrowDownUp: ['ArrowsDownUp', 'RiArrowUpDownFill'], ArrowLeft: ['ArrowLeft', 'RiArrowLeftLine'],
  ArrowLeftRight: ['ArrowsLeftRight', 'RiArrowLeftRightFill'], ArrowRight: ['ArrowRight', 'RiArrowRightLine'], ArrowRightLeft: ['ArrowsLeftRight', 'RiArrowLeftRightFill'],
  ArrowUp: ['ArrowUp', 'RiArrowUpLine'], ArrowUpCircle: ['ArrowCircleUp', 'RiArrowUpCircleFill'], ArrowUpRight: ['ArrowUpRight', 'RiArrowRightUpLine'],
  Asterisk: ['Asterisk', 'RiAsterisk'], AtSign: ['At', 'RiAtFill'], BadgeAlert: ['SealWarning', 'RiErrorWarningFill'], BadgeCheck: ['SealCheck', 'RiVerifiedBadgeFill'],
  Ban: ['Prohibit', 'RiForbid2Fill'], BarChart3: ['ChartBar', 'RiBarChart2Fill'], BellRing: ['BellRinging', 'RiNotification3Fill'], Blocks: ['SquaresFour', 'RiApps2Fill'],
  BookOpen: ['BookOpen', 'RiBookOpenFill'], Box: ['Cube', 'RiBox3Fill'], Boxes: ['Cube', 'RiBox1Fill'], Braces: ['BracketsCurly', 'RiBracesFill'], Bug: ['Bug', 'RiBugFill'],
  CalendarClock: ['CalendarDots', 'RiCalendarScheduleFill'], Check: ['Check', 'RiCheckLine'], CheckCircle2: ['CheckCircle', 'RiCheckboxCircleFill'],
  ChevronDown: ['CaretDown', 'RiArrowDownSLine'], ChevronLeft: ['CaretLeft', 'RiArrowLeftSLine'], ChevronRight: ['CaretRight', 'RiArrowRightSLine'], ChevronUp: ['CaretUp', 'RiArrowUpSLine'],
  ChevronsUpDown: ['CaretUpDown', 'RiExpandUpDownFill'], Circle: ['Circle', 'RiCircleFill'], CircleDashed: ['CircleDashed', 'RiLoader2Fill'], CircleHelp: ['Question', 'RiQuestionFill'],
  CirclePause: ['PauseCircle', 'RiPauseCircleFill'], CirclePlay: ['PlayCircle', 'RiPlayCircleFill'], Clock: ['Clock', 'RiTimeFill'], Clock3: ['Clock', 'RiTimeFill'],
  Cloud: ['Cloud', 'RiCloudFill'], CloudDownload: ['CloudArrowDown', 'RiDownloadCloud2Fill'], Code: ['Code', 'RiCodeSSlashFill'], Code2: ['CodeBlock', 'RiCodeBoxFill'], Cog: ['GearSix', 'RiSettings5Fill'],
  Columns3: ['Columns', 'RiLayoutColumnFill'], Compass: ['Compass', 'RiCompass3Fill'], Construction: ['Barricade', 'RiToolsFill'], Copy: ['Copy', 'RiFileCopyFill'],
  CornerDownLeft: ['ArrowBendDownLeft', 'RiCornerDownLeftFill'], Cpu: ['Cpu', 'RiCpuFill'], Database: ['Database', 'RiDatabase2Fill'], DatabaseBackup: ['ClockCounterClockwise', 'RiDatabaseFill'],
  DatabaseZap: ['Lightning', 'RiDatabase2Line'], Download: ['DownloadSimple', 'RiDownload2Fill'], DownloadCloud: ['CloudArrowDown', 'RiDownloadCloud2Fill'], ExternalLink: ['ArrowSquareOut', 'RiExternalLinkFill'],
  Eye: ['Eye', 'RiEyeFill'], EyeOff: ['EyeSlash', 'RiEyeOffFill'], File: ['File', 'RiFile3Fill'], FileArchive: ['FileArchive', 'RiFileZipFill'], FileCode: ['FileCode', 'RiFileCodeFill'],
  FileCode2: ['FileCode', 'RiFileCodeFill'], FileLock: ['FileLock', 'RiFileLockFill'], FileSearch: ['FileMagnifyingGlass', 'RiFileSearchFill'], FileText: ['FileText', 'RiFileTextFill'],
  FileWarning: ['FileX', 'RiFileWarningFill'], FileX2: ['FileX', 'RiFileCloseFill'], Flame: ['Fire', 'RiFireFill'], FlaskConical: ['Flask', 'RiFlaskFill'], Folder: ['Folder', 'RiFolder3Fill'],
  FolderCog: ['FolderUser', 'RiFolderSettingsFill'], FolderLock: ['FolderLock', 'RiFolderLockFill'], FolderOpen: ['FolderOpen', 'RiFolderOpenFill'], FolderTree: ['TreeStructure', 'RiNodeTree'],
  Forward: ['ShareFat', 'RiShareForwardFill'], Gauge: ['Gauge', 'RiDashboard3Fill'], GaugeCircle: ['Gauge', 'RiDashboard2Fill'], GitBranch: ['GitBranch', 'RiGitBranchFill'], Globe: ['Globe', 'RiGlobalFill'],
  Globe2: ['GlobeHemisphereWest', 'RiEarthFill'], HardDrive: ['HardDrive', 'RiHardDrive2Fill'], HardDriveDownload: ['HardDrives', 'RiHardDrive3Fill'], HeartPulse: ['Heartbeat', 'RiHeartPulseFill'],
  History: ['ClockCounterClockwise', 'RiHistoryFill'], Home: ['House', 'RiHome4Fill'], Inbox: ['Tray', 'RiInboxFill'], Info: ['Info', 'RiInformationFill'], KeyRound: ['Key', 'RiKey2Fill'], KeySquare: ['Password', 'RiKeyFill'],
  Layers: ['Stack', 'RiStackFill'], LayoutDashboard: ['SquaresFour', 'RiDashboardFill'], LayoutGrid: ['GridFour', 'RiLayoutGridFill'], LineChart: ['ChartLine', 'RiLineChartFill'], Link2: ['Link', 'RiLinksFill'],
  ListChecks: ['ListChecks', 'RiListCheck3'], Loader2: ['CircleNotch', 'RiLoader4Line'], Lock: ['Lock', 'RiLockFill'], LockKeyhole: ['LockKey', 'RiLockPasswordFill'], LogIn: ['SignIn', 'RiLoginBoxFill'],
  LogOut: ['SignOut', 'RiLogoutBoxRFill'], Mail: ['Envelope', 'RiMailFill'], MailCheck: ['EnvelopeOpen', 'RiMailCheckFill'], MemoryStick: ['Memory', 'RiRam2Fill'], Menu: ['List', 'RiMenuLine'], Minus: ['Minus', 'RiSubtractLine'],
  Monitor: ['Monitor', 'RiComputerFill'], MonitorSmartphone: ['Devices', 'RiDeviceFill'], Moon: ['Moon', 'RiMoonFill'], MoreHorizontal: ['DotsThree', 'RiMoreFill'], Network: ['Network', 'RiNodeTree'], Package: ['Package', 'RiBox3Fill'],
  PackageCheck: ['Package', 'RiBox3Line'], Paintbrush: ['PaintBrush', 'RiBrushFill'], Palette: ['Palette', 'RiPaletteFill'], PanelLeftClose: ['SidebarSimple', 'RiSideBarFill'], PanelLeftOpen: ['SidebarSimple', 'RiSideBarLine'],
  Pause: ['Pause', 'RiPauseFill'], PauseCircle: ['PauseCircle', 'RiPauseCircleFill'], Pencil: ['PencilSimple', 'RiPencilFill'], Play: ['Play', 'RiPlayFill'], PlayCircle: ['PlayCircle', 'RiPlayCircleFill'], Plug: ['Plug', 'RiPlugFill'],
  Plus: ['Plus', 'RiAddLine'], Power: ['Power', 'RiShutDownFill'], Puzzle: ['PuzzlePiece', 'RiPuzzle2Fill'], RefreshCw: ['ArrowsClockwise', 'RiRefreshFill'], Reply: ['ArrowBendUpLeft', 'RiReplyFill'], Rocket: ['Rocket', 'RiRocket2Fill'],
  RotateCcw: ['ArrowCounterClockwise', 'RiArrowGoBackFill'], RotateCw: ['ArrowClockwise', 'RiArrowGoForwardFill'], Route: ['Path', 'RiRouteFill'], Save: ['FloppyDisk', 'RiSave3Fill'], ScrollText: ['Scroll', 'RiFileList3Fill'],
  Search: ['MagnifyingGlass', 'RiSearchLine'], Send: ['PaperPlaneTilt', 'RiSendPlaneFill'], Server: ['HardDrives', 'RiServerFill'], ServerCog: ['GearFine', 'RiServerLine'], ServerCrash: ['Warning', 'RiServerFill'], Settings: ['Gear', 'RiSettings3Fill'],
  Settings2: ['GearSix', 'RiSettings4Fill'], Share2: ['ShareNetwork', 'RiShareFill'], Shield: ['Shield', 'RiShieldFill'], ShieldAlert: ['ShieldWarning', 'RiShieldFlashFill'], ShieldBan: ['ShieldSlash', 'RiShieldCrossFill'],
  ShieldCheck: ['ShieldCheck', 'RiShieldCheckFill'], ShieldHalf: ['ShieldChevron', 'RiShieldUserFill'], ShieldOff: ['ShieldSlash', 'RiShieldLine'], ShieldPlus: ['ShieldPlus', 'RiShieldStarFill'], ShieldX: ['ShieldSlash', 'RiShieldCrossFill'],
  Signpost: ['Signpost', 'RiSignpostFill'], Skull: ['Skull', 'RiSkullFill'], SlidersHorizontal: ['SlidersHorizontal', 'RiEqualizerFill'], Smartphone: ['DeviceMobile', 'RiSmartphoneFill'], Square: ['Square', 'RiCheckboxBlankFill'],
  SquareTerminal: ['TerminalWindow', 'RiTerminalBoxFill'], Stethoscope: ['Stethoscope', 'RiStethoscopeFill'], StickyNote: ['Note', 'RiStickyNoteFill'], Store: ['Storefront', 'RiStore2Fill'], Sun: ['Sun', 'RiSunFill'], Target: ['Target', 'RiFocus3Fill'],
  TerminalSquare: ['TerminalWindow', 'RiTerminalBoxFill'], TimerReset: ['Timer', 'RiTimerFill'], Trash2: ['Trash', 'RiDeleteBin6Fill'], TriangleAlert: ['Warning', 'RiAlertFill'], Trophy: ['Trophy', 'RiTrophyFill'], Undo2: ['ArrowUUpLeft', 'RiArrowGoBackLine'],
  Upload: ['UploadSimple', 'RiUpload2Fill'], User: ['User', 'RiUser3Fill'], UserCog: ['UserGear', 'RiUserSettingsFill'], UserPlus: ['UserPlus', 'RiUserAddFill'], UserRound: ['UserCircle', 'RiAccountCircleFill'],
  UserRoundCog: ['UserCircleGear', 'RiUserSettingsFill'], Users: ['Users', 'RiGroupFill'], Wand2: ['MagicWand', 'RiMagicFill'], Waypoints: ['Graph', 'RiMapPin2Fill'], Webhook: ['WebhooksLogo', 'RiWebhookFill'], Workflow: ['FlowArrow', 'RiFlowChart'],
  Wrench: ['Wrench', 'RiToolsFill'], X: ['X', 'RiCloseLine'], XCircle: ['XCircle', 'RiCloseCircleFill'], Zap: ['Lightning', 'RiFlashlightFill'],
}

// Grid's tool pictograms: a solid Remix glyph on a coloured tile (grid/tile.jsx),
// as [glyph, tone]. grid.css draws the tones listed in GRID_TONES.
export const GRID_TONES = ['blue', 'cyan', 'teal', 'green', 'amber', 'red', 'pink', 'violet', 'indigo', 'slate']
export const GRID_TOOL_ICONS = {
  '/domains': ['RiGlobalFill', 'blue'], '/subdomains': ['RiNodeTree', 'cyan'], '/ftp': ['RiFolderTransferFill', 'teal'], '/ssl': ['RiLockFill', 'green'],
  '/databases': ['RiDatabase2Fill', 'amber'], '/dns': ['RiSignpostFill', 'violet'], '/php': ['RiPhpFill', 'indigo'],

  '/email': ['RiMailFill', 'blue'], '/email?webmail=1': ['RiMailOpenFill', 'cyan'], '/email/settings': ['RiMailSettingsFill', 'slate'], '/email/dns': ['RiMailCheckFill', 'green'],
  '/email/spam': ['RiMailForbidFill', 'red'], '/email/migration': ['RiMailDownloadFill', 'violet'],

  '/files': ['RiFolderOpenFill', 'amber'], '/files?tool=permissions': ['RiFolderKeyholeFill', 'indigo'], '/redirects': ['RiDirectionFill', 'teal'], '/forwarding': ['RiLinksFill', 'blue'],
  '/cache': ['RiFlashlightFill', 'violet'], '/website-maintenance': ['RiToolsFill', 'slate'], '/error-pages': ['RiFileWarningFill', 'red'],

  '/cron': ['RiAlarmFill', 'pink'], '/ssh': ['RiKey2Fill', 'amber'], '/terminal': ['RiTerminalBoxFill', 'slate'], '/logs': ['RiFileList3Fill', 'blue'], '/devtools': ['RiCodeSSlashFill', 'violet'],
  '/change-password': ['RiLockPasswordFill', 'indigo'], '/appearance': ['RiPaletteFill', 'pink'],

  '/disk-usage': ['RiPieChart2Fill', 'blue'], '/website-statistics': ['RiBarChartBoxFill', 'green'], '/processes': ['RiCpuFill', 'indigo'], '/malware': ['RiBugFill', 'red'],
  '/website-security': ['RiShieldCheckFill', 'teal'], '/security': ['RiFingerprintFill', 'violet'], '/site-health': ['RiStethoscopeFill', 'pink'],

  '/accounts': ['RiGroupFill', 'blue'], '/accounts/new': ['RiUserAddFill', 'green'], '/resellers': ['RiStore2Fill', 'amber'], '/administrators': ['RiAdminFill', 'violet'],
  '/plans': ['RiPriceTag3Fill', 'pink'], '/import/accounts': ['RiUserSharedFill', 'teal'],

  '/health': ['RiPulseFill', 'green'], '/diagnostics': ['RiStethoscopeFill', 'cyan'], '/services': ['RiServerFill', 'blue'], '/openlitespeed': ['RiFlashlightFill', 'amber'],
  '/resource-manager': ['RiEqualizerFill', 'indigo'], '/filesystem-isolation': ['RiFolderShield2Fill', 'slate'], '/security-center': ['RiShieldStarFill', 'red'], '/stack-manager': ['RiStackFill', 'violet'],
  '/db-monitor': ['RiDatabase2Fill', 'teal'], '/slow-queries': ['RiHourglassFill', 'pink'], '/updates': ['RiRefreshFill', 'blue'],

  '/dns-setup': ['RiSignpostFill', 'violet'], '/cloudflare': ['RiCloudFill', 'amber'], '/dns-cluster': ['RiShareFill', 'indigo'], '/ip-management': ['RiMapPin2Fill', 'teal'],

  '/mail-queue': ['RiMailSendFill', 'blue'], '/mail-tracking': ['RiSearchEyeFill', 'cyan'], '/imap-migrations': ['RiMailDownloadFill', 'violet'], '/notifications': ['RiNotification3Fill', 'amber'],

  '/templates': ['RiLayoutMasonryFill', 'pink'], '/maintenance-mode': ['RiToolsFill', 'slate'],

  '/firewall': ['RiFireFill', 'red'], '/fail2ban': ['RiUserForbidFill', 'amber'], '/waf': ['RiShieldFlashFill', 'indigo'], '/ip-bans': ['RiForbid2Fill', 'pink'],
  '/ip-whitelist': ['RiCheckboxCircleFill', 'green'], '/tokens': ['RiKeyFill', 'slate'],

  '/bandwidth': ['RiSpeedUpFill', 'blue'], '/site-stats': ['RiLineChartFill', 'green'], '/audit-log': ['RiFileShield2Fill', 'indigo'], '/account-log': ['RiFileUserFill', 'teal'],
  '/error-log': ['RiFileWarningFill', 'red'], '/retention': ['RiArchiveDrawerFill', 'slate'], '/webhooks': ['RiWebhookFill', 'pink'],

  '/server-setup': ['RiInstallFill', 'violet'], '/panel-settings': ['RiSettings3Fill', 'slate'], '/panel-theme': ['RiLayout4Fill', 'blue'], '/branding': ['RiQuillPenFill', 'amber'],
  '/license': ['RiAwardFill', 'green'], '/api/docs': ['RiBookOpenFill', 'teal'],

  '/backups': ['RiHistoryFill', 'violet'], '/backups?component=files': ['RiFolderZipFill', 'amber'], '/backups?component=databases': ['RiDatabase2Fill', 'indigo'],
  '/backups?component=mail': ['RiInboxArchiveFill', 'blue'], '/backups?action=create&kind=full': ['RiArchiveFill', 'teal'], '/backups?view=restores': ['RiDownloadCloud2Fill', 'cyan'],

  '/backup-jobs': ['RiHistoryFill', 'violet'], '/backup-jobs?tab=jobs': ['RiCalendarScheduleFill', 'blue'], '/backup-jobs?tab=destinations': ['RiHardDrive3Fill', 'teal'],
  '/backup-jobs?tab=history&view=recovery': ['RiInboxUnarchiveFill', 'green'], '/backup-jobs?tab=history&view=activity': ['RiFileList3Fill', 'slate'], '/backup-jobs?tab=notifications': ['RiNotification3Fill', 'amber'],
}
