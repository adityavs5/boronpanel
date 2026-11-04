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

// Grid's home tiles and page headers use full-colour pictograms (Flat Color
// Icons), one per tool route. A route without one falls back to its Remix icon.
export const GRID_TOOL_ICONS = {
  '/domains': 'FcGlobe', '/subdomains': 'FcTreeStructure', '/ftp': 'FcUpload', '/ssl': 'FcLock', '/databases': 'FcDatabase', '/dns': 'FcFlowChart', '/php': 'FcCommandLine',
  '/email': 'FcInvite', '/email?webmail=1': 'FcFeedback', '/email/settings': 'FcDataConfiguration', '/email/dns': 'FcApproval', '/email/spam': 'FcCancel', '/email/migration': 'FcCollect',
  '/files': 'FcOpenedFolder', '/files?tool=permissions': 'FcPrivacy', '/redirects': 'FcAdvance', '/forwarding': 'FcLink', '/cache': 'FcFlashOn', '/website-maintenance': 'FcSupport', '/error-pages': 'FcHighPriority',
  '/cron': 'FcAlarmClock', '/ssh': 'FcKey', '/terminal': 'FcCommandLine', '/logs': 'FcViewDetails', '/devtools': 'FcEngineering', '/change-password': 'FcKey', '/appearance': 'FcTemplate',
  '/disk-usage': 'FcPieChart', '/website-statistics': 'FcBarChart', '/processes': 'FcProcess', '/malware': 'FcBiohazard', '/website-security': 'FcDataProtection', '/security': 'FcLock', '/site-health': 'FcLike',
  
  '/accounts': 'FcConferenceCall', '/accounts/new': 'FcBusinessman', '/resellers': 'FcBusiness', '/administrators': 'FcManager', '/plans': 'FcPackage', '/import/accounts': 'FcImport',
  '/health': 'FcLineChart', '/diagnostics': 'FcInspection', '/resource-manager': 'FcElectronics', '/filesystem-isolation': 'FcLockPortrait', '/security-center': 'FcDataProtection',
  '/stack-manager': 'FcDeployment', '/services': 'FcServices', '/openlitespeed': 'FcFlashAuto', '/db-monitor': 'FcDataSheet', '/slow-queries': 'FcOvertime', '/dns-setup': 'FcWorkflow', '/cloudflare': 'FcDataEncryption',
  '/dns-cluster': 'FcMindMap', '/ip-management': 'FcOrgUnit', '/updates': 'FcRefresh', '/mail-queue': 'FcInTransit', '/mail-tracking': 'FcBinoculars', '/imap-migrations': 'FcCollect', '/notifications': 'FcAdvertising',
  '/templates': 'FcTemplate', '/maintenance-mode': 'FcSupport', '/firewall': 'FcBadDecision', '/fail2ban': 'FcDisapprove', '/waf': 'FcGoodDecision', '/ip-bans': 'FcCancel', '/ip-whitelist': 'FcApprove', '/tokens': 'FcKey',
  '/bandwidth': 'FcComboChart', '/site-stats': 'FcAreaChart', '/audit-log': 'FcTodoList', '/account-log': 'FcSurvey', '/error-log': 'FcHighPriority', '/retention': 'FcFilingCabinet', '/webhooks': 'FcShare',
  '/panel-settings': 'FcSettings', '/server-setup': 'FcAutomatic', '/branding': 'FcEditImage', '/panel-theme': 'FcTemplate', '/api/docs': 'FcLibrary', '/license': 'FcDiploma2',
  
  '/backups': 'FcDataBackup', '/backups?component=files': 'FcFolder', '/backups?component=databases': 'FcDataRecovery', '/backups?component=mail': 'FcInvite', '/backups?action=create&kind=full': 'FcDataBackup', '/backups?view=restores': 'FcDownload',
  '/backup-jobs': 'FcDataBackup', '/backup-jobs?tab=jobs': 'FcPlanner', '/backup-jobs?tab=destinations': 'FcFilingCabinet', '/backup-jobs?tab=history&view=recovery': 'FcDataRecovery', '/backup-jobs?tab=history&view=activity': 'FcTodoList', '/backup-jobs?tab=notifications': 'FcAdvertising',
}
