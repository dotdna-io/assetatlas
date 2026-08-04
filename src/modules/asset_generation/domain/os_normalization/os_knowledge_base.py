#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Comprehensive OS Knowledge Base
Compiled from multiple sources:
- IANA Operating System Names Registry
- Nmap OS fingerprints
- CPE Dictionary (NIST)
- AssetAtlas training data
- Qualys OS list
"""

# =============================================================================
# IANA OFFICIAL OS NAMES (RFC952)
# =============================================================================
IANA_OS_NAMES = {
    'AEGIS', 'AMIGA-OS-1.2', 'AMIGA-OS-1.3', 'AMIGA-OS-2.0', 'AMIGA-OS-2.1',
    'AMIGA-OS-3.0', 'AMIGA-OS-3.1', 'AMIGA-OS-3.5', 'AMIGA-OS-3.9', 'APOLLO',
    'AIX/370', 'AIX-PS/2', 'BEOS-4.5.2', 'BEOS-5.0', 'BS-2000', 'CEDAR',
    'CGW', 'CHORUS', 'CHRYSALIS', 'CMOS', 'CMS', 'COS', 'CPIX', 'CTOS',
    'CTSS', 'CYGWIN', 'DCN', 'DDNOS', 'DOMAIN', 'DOS', 'ECOS', 'ECOSPRO',
    'EDX', 'ELF', 'EMBOS', 'EMMOS', 'EPOS', 'FOONEX', 'FREEBSD', 'FORTH',
    'FUZZ', 'GCOS', 'GPOS', 'HDOS', 'IMAGEN', 'INSTANT-INTERNET', 'INTERCOM',
    'IMPRESS', 'INTERLISP', 'IOS', 'IRIX', 'ISI-68020', 'ITS', 'KOSOS',
    'LINUX', 'LINUX-1.0', 'LINUX-1.2', 'LINUX-2.0', 'LINUX-2.2', 'LINUX-2.4',
    'LINUX-2.6', 'LISP', 'LISPM', 'LOCUS', 'MACOS', 'MINOS', 'MOS', 'MPE5',
    'MPE/V', 'MPE/IX', 'MSDOS', 'MULTICS', 'MUSIC', 'MUSIC/SP', 'MVS', 'MVS/SP',
    'NETBSD-1.0', 'NETBSD-1.1', 'NETBSD-1.2', 'NETBSD-1.3', 'NETWARE-3',
    'NETWARE-3.11', 'NETWARE-4.0', 'NETWARE-4.1', 'NETWARE-5.0', 'NEXUS',
    'NMS', 'NONSTOP', 'NOS-2', 'NTOS', 'OPENBSD', 'OPENVME', 'OPENVMS',
    'OS/DDP', 'OS/2', 'OS4', 'OS86', 'OSX', 'PCDOS', 'PERQ/OS', 'PLI',
    'PSDOS/MIT', 'PSOS', 'PRIMOS', 'RISC-OS', 'RISC-OS-3.10', 'RISC-OS-3.50',
    'RISC-OS-3.60', 'RISC-OS-3.70', 'RISC-OS-4.00', 'RMX/RDOS', 'ROS',
    'RSX11M', 'RTE-A', 'SATOPS', 'SINIX', 'SCO-OPEN-DESKTOP-1.0',
    'SCO-OPEN-DESKTOP-1.1', 'SCO-OPEN-DESKTOP-2.0', 'SCO-OPEN-DESKTOP-3.0',
    'SCO-OPEN-DESKTOP-LITE-3.0', 'SCO-OPEN-SERVER-3.0', 'SCO-UNIX-3.2.0',
    'SCO-UNIX-3.2V2.0', 'SCO-UNIX-3.2V2.1', 'SCO-UNIX-3.2V4.0',
    'SCO-UNIX-3.2V4.1', 'SCO-UNIX-3.2V4.2', 'SCO-XENIX-386-2.3.2',
    'SCO-XENIX-386-2.3.3', 'SCO-XENIX-386-2.3.4', 'SCS', 'SIMP', 'SUN',
    'SUN-OS-3.5', 'SUN-OS-4.0', 'SWIFT', 'TAC', 'TANDEM', 'TENEX',
    'THE-MAJOR-BBS', 'TOPS10', 'TOPS20', 'TOS', 'TP3010', 'TRSDOS', 'ULTRIX',
    'UNIX', 'UNIX-BSD', 'UNIX-V1AT', 'UNIX-V', 'UNIX-V.1', 'UNIX-V.2',
    'UNIX-V.3', 'UNIX-PC', 'UNKNOWN', 'UT2D', 'V', 'VM', 'VM/370', 'VM/CMS',
    'VM/SP', 'VMS', 'VMS/EUNICE', 'VRTX', 'WAITS', 'WANG', 'WIN32',
    'WINDOWS-95', 'WINDOWS-95-OSR1', 'WINDOWS-95-OSR2', 'WINDOWS-98',
    'WINDOWS-CE', 'WINDOWS-NT', 'WINDOWS-NT-2', 'WINDOWS-NT-3',
    'WINDOWS-NT-3.5', 'WINDOWS-NT-3.51', 'WINDOWS-NT-4', 'WINDOWS-NT-5',
    'WINDOWS-NT-5.1', 'WINDOWS-NT-6', 'WINDOWS-NT-6.1', 'WORLDGROUP',
    'WYSE-WYXWARE', 'X11R3', 'XDE', 'XENIX'
}

# =============================================================================
# CPE VENDOR MAPPINGS (for CPE 2.3 format)
# =============================================================================
CPE_VENDOR_MAP = {
    # Microsoft variations
    'microsoft': 'microsoft',
    'ms': 'microsoft',
    'msft': 'microsoft',

    # Apple variations
    'apple': 'apple',
    'mac': 'apple',

    # Linux distributions
    'redhat': 'redhat',
    'red hat': 'redhat',
    'canonical': 'canonical',
    'ubuntu': 'canonical',
    'centos': 'centos',
    'fedora': 'fedora',
    'suse': 'suse',
    'opensuse': 'opensuse',
    'debian': 'debian',
    'oracle': 'oracle',
    'amazon': 'amazon',

    # Network vendors
    'cisco': 'cisco',
    'juniper': 'juniper',
    'arista': 'arista',
    'fortinet': 'fortinet',
    'paloalto': 'palo_alto_networks',
    'palo alto': 'palo_alto_networks',
    'checkpoint': 'checkpoint',
    'check point': 'checkpoint',

    # Virtualization
    'vmware': 'vmware',
    'citrix': 'citrix',
    'proxmox': 'proxmox',

    # BSD
    'freebsd': 'freebsd',
    'openbsd': 'openbsd',
    'netbsd': 'netbsd',

    # Mobile
    'google': 'google',
    'android': 'google',

    # Unix vendors
    'sun': 'oracle',
    'sun microsystems': 'oracle',
    'ibm': 'ibm',
    'hp': 'hp',
    'hewlett-packard': 'hp',
    'hewlett packard': 'hp',

    # Embedded vendors
    'wind river': 'windriver',
    'blackberry': 'blackberry',
    'huawei': 'huawei',
}

# =============================================================================
# CPE PRODUCT MAPPINGS
# =============================================================================
CPE_PRODUCT_MAP = {
    # Windows
    'windows 11': 'windows_11',
    'windows 10': 'windows_10',
    'windows 8.1': 'windows_8.1',
    'windows 8': 'windows_8',
    'windows 7': 'windows_7',
    'windows vista': 'windows_vista',
    'windows xp': 'windows_xp',
    'windows server 2025': 'windows_server_2025',
    'windows server 2022': 'windows_server_2022',
    'windows server 2019': 'windows_server_2019',
    'windows server 2016': 'windows_server_2016',
    'windows server 2012': 'windows_server_2012',
    'windows server 2008': 'windows_server_2008',
    'windows server 2003': 'windows_server_2003',
    'windows nt': 'windows_nt',
    'windows nt 3': 'windows_nt_3',
    'windows nt 4': 'windows_nt_4',
    'windows 2000': 'windows_2000',
    'windows 2003': 'windows_server_2003',
    'windows 3.1': 'windows_3',
    'windows 3.11': 'windows_3',
    'windows 95': 'windows_95',
    'windows 98': 'windows_98',
    'windows me': 'windows_me',
    'windows ce': 'windows_ce',
    'windows mobile': 'windows_mobile',
    'windows phone': 'windows_phone',
    'windowsholographic': 'windows_holographic',

    # macOS
    'macos': 'mac_os_x',
    'mac os x': 'mac_os_x',
    'mac os': 'mac_os',
    'osx': 'mac_os_x',

    # Linux
    'rhel': 'enterprise_linux',
    'red hat enterprise linux': 'enterprise_linux',
    'centos': 'centos',
    'ubuntu': 'ubuntu_linux',
    'debian': 'debian_linux',
    'fedora': 'fedora',
    'suse linux': 'suse_linux_enterprise_server',
    'suse': 'suse_linux_enterprise_server',
    'sles': 'suse_linux_enterprise_server',
    'opensuse': 'opensuse',
    'amazon linux': 'amazon_linux',
    'oracle linux': 'linux',
    'rocky linux': 'rocky_linux',
    'almalinux': 'almalinux',
    'arch linux': 'arch_linux',
    'linux mint': 'linux_mint',
    'linux': 'linux',  # Generic Linux
    'gnu/linux': 'linux',

    # Unix
    'solaris': 'solaris',
    'sunos': 'sunos',
    'aix': 'aix',
    'hp-ux': 'hp-ux',
    'hpux': 'hp-ux',

    # BSD
    'freebsd': 'freebsd',
    'openbsd': 'openbsd',
    'netbsd': 'netbsd',

    # Network OS
    'cisco ios': 'ios',
    'ios-xe': 'ios_xe',
    'ios-xr': 'ios_xr',
    'ios xe': 'ios_xe',
    'ios xr': 'ios_xr',
    'nx-os': 'nx-os',
    'asa': 'adaptive_security_appliance_software',
    'junos': 'junos',
    'junos os': 'junos',
    'eos': 'eos',
    'pan-os': 'pan-os',
    'panos': 'pan-os',
    'fortios': 'fortios',
    'gaia': 'gaia_os',

    # Mobile
    'ios': 'iphone_os',  # Apple iOS
    'ipados': 'ipados',
    'android': 'android',
    'symbian': 'symbian',
    'blackberry': 'blackberry',
    'harmonyos': 'harmonyos',

    # Embedded/IoT
    'vxworks': 'vxworks',
    'qnx': 'qnx',
    'freertos': 'freertos',
    'zephyr': 'zephyr',
    'threadx': 'threadx',
    'tinyos': 'tinyos',
    'contiki': 'contiki',
    'riot': 'riot',

    # Virtualization
    'esxi': 'esxi',
    'vsphere': 'vsphere',
    'vmware': 'vmware',
    'xen': 'xen',
    'kvm': 'kvm',
}

# =============================================================================
# OS FAMILY DETECTION PATTERNS
# =============================================================================
OS_FAMILY_PATTERNS = {
    'windows_desktop': {
        'patterns': [
            r'windows\s*(?:11|10|8\.1|8|7|vista|xp|me|98|95)',
            r'win\s*(?:11|10|8|7)',
            r'windows\s+nt\s+(?:10|6\.|5\.)',
            r'windows\s+(?:10\.0|6\.[0-3])',  # NT version format (6.0-6.3, 10.0)
            # Handle concatenated versions
            r'windows11',
            r'windows10',
            r'windows8\.1',
            r'windows8',
            r'windows7',
            r'win11',
            r'win10',
            r'win8',
            r'win7',
        ],
        'exclude': [r'server', r'iot', r'embedded', r'mobile'],
        'category': 'workstation'
    },
    'windows_server': {
        'patterns': [
            r'windows\s*server',
            r'server\s*(?:2025|2022|2019|2016|2012|2008|2003)',
            r'windows\s+nt.*(?:server|datacenter)',
            # Bare "windows YYYY" without "server" keyword — year numbers
            # (2008-2025) always refer to Windows Server (#161 QA-13)
            r'windows\s+(?:2025|2022|2019|2016|2012|2008)\b',
            # Handle concatenated versions
            r'windowsserver2025',
            r'windowsserver2022',
            r'windowsserver2019',
            r'windowsserver2016',
            r'windowsserver2012r2',
            r'windowsserver2012',
            r'windowsserver2008r2',
            r'windowsserver2008',
            r'windowsserver2003',
            r'winserver2025',
            r'winserver2022',
            r'winserver2019',
            r'winserver2016',
            r'winserver2012',
            r'winserver2008',
        ],
        'exclude': [],
        'category': 'server'
    },
    'windows_embedded': {
        'patterns': [
            r'windows\s+(?:ce|iot|embedded)',
            r'windows\s+mobile',
        ],
        'exclude': [],
        'category': 'iot'
    },
    'windows_nt_legacy': {
        'patterns': [
            r'windows\s+nt\s+(?:3\.1|3\.5|3\.51|4\.0|4)',
            r'nt\s+(?:3\.|4\.)',
        ],
        'exclude': ['server'],
        'category': 'workstation'
    },
    'windows_2000': {
        'patterns': [
            r'windows\s+2000',
            r'win2k',
            r'w2k',
        ],
        'exclude': [],
        'category': 'workstation'
    },
    'windows_2003_server': {
        'patterns': [
            r'windows\s+(?:server\s+)?2003',
            r'win2003',
            r'w2k3',
            r'windows2003',  # Concatenated version
        ],
        'exclude': [],
        'category': 'server'
    },
    'windows_ancient': {
        'patterns': [
            r'windows\s+(?:3\.1|3\.11)',
            r'windows\s+for\s+workgroups',
            r'win\s*3\.1',
        ],
        'exclude': [],
        'category': 'workstation'
    },
    'linux_rhel': {
        'patterns': [
            r'red\s*hat',
            r'rhel',
            r'redhat',
            r'enterprise\s+linux',
        ],
        'exclude': [],
        'category': 'server'
    },
    'linux_ubuntu': {
        'patterns': [r'ubuntu'],
        'exclude': [],
        'category': 'server'  # Unless desktop keyword present
    },
    'linux_centos': {
        'patterns': [r'centos'],
        'exclude': [],
        'category': 'server'
    },
    'linux_debian': {
        'patterns': [r'debian'],
        'exclude': [],
        'category': 'server'
    },
    'linux_amazon': {
        'patterns': [r'amazon\s+linux', r'amzn'],
        'exclude': [],
        'category': 'server'
    },
    'linux_oracle': {
        'patterns': [r'oracle\s+(?:linux|enterprise)'],
        'exclude': [],
        'category': 'server'
    },
    'linux_rocky': {
        'patterns': [r'rocky\s+linux', r'rocky'],
        'exclude': [],
        'category': 'server'
    },
    'linux_almalinux': {
        'patterns': [r'almalinux', r'alma\s+linux'],
        'exclude': [],
        'category': 'server'
    },
    'linux_suse': {
        'patterns': [r'suse', r'sles', r'opensuse'],
        'exclude': [],
        'category': 'server'
    },
    'linux_fedora': {
        'patterns': [r'fedora'],
        'exclude': [],
        'category': 'workstation'
    },
    'linux_arch': {
        'patterns': [r'arch\s+linux', r'\barch\b'],
        'exclude': [],
        'category': 'workstation'
    },
    'linux_mint': {
        'patterns': [r'linux\s+mint', r'\bmint\b'],
        'exclude': [],
        'category': 'workstation'
    },
    'macos': {
        'patterns': [
            r'mac\s*os',
            r'osx',
            r'darwin',
            r'macmdm',  # Apple MDM protocol version string — underlying OS is macOS (#58 Item 3)
        ],
        'exclude': [],
        'category': 'workstation'
    },
    'mobile_ios': {
        'patterns': [
            r'(?:iphone|ipad|ipados)',
            r'\bios\b(?!\s*xe)',  # iOS but not IOS XE
            # Handle concatenated versions
            r'ios\d+',  # ios14, ios15, etc.
            r'ipados\d+',  # ipados14, ipados15, etc.
            r'appleios',  # Concatenated Apple iOS
        ],
        'exclude': [r'cisco'],
        'category': 'mobile'
    },
    'mobile_android': {
        'patterns': [r'android'],
        'exclude': [],
        'category': 'mobile'
    },
    'mobile_windows': {
        'patterns': [
            r'windows\s+(?:mobile|phone|ce)',
            r'windows\s+(?:10|8)\s+mobile',
            r'pocket\s+pc',
        ],
        'exclude': [],
        'category': 'mobile'
    },
    'mobile_symbian': {
        'patterns': [r'symbian', r'\bs60\b'],
        'exclude': [],
        'category': 'mobile'
    },
    'mobile_blackberry': {
        'patterns': [r'blackberry', r'\bbb10\b', r'rim\s+os'],
        'exclude': [],
        'category': 'mobile'
    },
    'mobile_harmonyos': {
        'patterns': [r'harmonyos', r'hongmeng'],
        'exclude': [],
        'category': 'mobile'
    },
    'mobile_windows_holographic': {
        'patterns': [r'windows\s*holographic', r'hololens'],
        'exclude': [],
        'category': 'mobile'
    },
    'network_cisco_ios': {
        'patterns': [
            r'cisco\s+ios(?!\s*x)',  # IOS but not IOS-XE/XR
            r'\bios\b(?=.*cisco)',
            r'ciscoios',  # Concatenated version
            # Cisco IOS train format as the LEADING token, e.g. "15.2(2)e7, release
            # software (fc3)". Anchored (^) so a parenthesized version that merely
            # appears inside another vendor's string (e.g. "pan-os 10.1(3)",
            # "nx-os 9.3(5)", "citrix adc 13.1(2)") is NOT stolen by this family,
            # which is iterated before the other network/unix/embedded families. (#681 D2)
            r'^\s*\d+\.\d+\(\d+\)',
        ],
        'exclude': [],
        'category': 'network'
    },
    'network_cisco_iosxe': {
        'patterns': [
            r'ios[_\s-]?xe',
            r'ios-xe',
            r'ciscoxe',  # Concatenated version
        ],
        'exclude': [],
        'category': 'network'
    },
    'network_cisco_iosxr': {
        'patterns': [r'ios[_\s-]?xr', r'ios-xr'],
        'exclude': [],
        'category': 'network'
    },
    'network_cisco_nxos': {
        'patterns': [r'nx-?os', r'nexus'],
        'exclude': [],
        'category': 'network'
    },
    'network_cisco_asa': {
        'patterns': [r'\basa\b.*cisco', r'cisco.*\basa\b'],
        'exclude': [],
        'category': 'network'
    },
    'network_juniper_junos': {
        'patterns': [r'junos', r'juniper', r'juno\s+os'],
        'exclude': [],
        'category': 'network'
    },
    'network_arista_eos': {
        'patterns': [r'arista', r'eos(?=.*arista)'],
        'exclude': [],
        'category': 'network'
    },
    'network_paloalto_panos': {
        'patterns': [r'pan-?os', r'palo\s+alto'],
        'exclude': [],
        'category': 'network'
    },
    'network_fortinet_fortios': {
        'patterns': [r'fortios', r'fortigate'],
        'exclude': [],
        'category': 'network'
    },
    'network_checkpoint_gaia': {
        'patterns': [r'gaia', r'check\s+point'],
        'exclude': [],
        'category': 'network'
    },
    'network_citrix_netscaler': {
        'patterns': [r'netscaler', r'citrix\s+(?:adc|netscaler)'],
        'exclude': [],
        'category': 'network'
    },
    'virtualization_vmware': {
        'patterns': [r'esxi', r'vsphere', r'vmware'],
        'exclude': [],
        'category': 'server'
    },
    'unix_solaris': {
        'patterns': [r'solaris', r'sunos', r'opensolaris'],
        'exclude': [],
        'category': 'server'
    },
    'unix_aix': {
        'patterns': [r'\baix\b'],
        'exclude': [],
        'category': 'server'
    },
    'unix_hpux': {
        'patterns': [r'hp-?ux', r'hpux'],
        'exclude': [],
        'category': 'server'
    },
    'bsd_freebsd': {
        'patterns': [r'freebsd'],
        'exclude': [],
        'category': 'server'
    },
    'bsd_openbsd': {
        'patterns': [r'openbsd'],
        'exclude': [],
        'category': 'server'
    },
    'bsd_netbsd': {
        'patterns': [r'netbsd'],
        'exclude': [],
        'category': 'server'
    },
    'bsd': {
        'patterns': [r'bsd'],
        'exclude': [],
        'category': 'server'
    },
    'linux_generic': {
        'patterns': [r'linux', r'\bgnu\b'],
        'exclude': [],
        'category': 'server'
    },
    'windows_generic': {
        'patterns': [
            r'^windows$',  # Just "windows" with no version
        ],
        'exclude': [],
        'category': 'workstation'
    },
    'unknown_os': {
        'patterns': [
            r'\bunknown\b',
            r'\bgeneric\b',
            r'^n/a$',
            r'^none$',
        ],
        'exclude': [],
        'category': 'unknown'
    },
    'embedded_vxworks': {
        'patterns': [r'vxworks', r'wind\s+river'],
        'exclude': [],
        'category': 'iot'
    },
    'embedded_qnx': {
        'patterns': [r'\bqnx\b'],
        'exclude': [],
        'category': 'iot'
    },
    'embedded_freertos': {
        'patterns': [r'freertos', r'free\s+rtos'],
        'exclude': [],
        'category': 'iot'
    },
    'embedded_zephyr': {
        'patterns': [r'zephyr'],
        'exclude': [],
        'category': 'iot'
    },
    'embedded_threadx': {
        'patterns': [r'threadx', r'azure\s+rtos'],
        'exclude': [],
        'category': 'iot'
    },
    'embedded_tinyos': {
        'patterns': [r'tinyos'],
        'exclude': [],
        'category': 'iot'
    },
    'embedded_contiki': {
        'patterns': [r'contiki'],
        'exclude': [],
        'category': 'iot'
    },
    'embedded_riot': {
        'patterns': [r'riot\s+os', r'\briot\b'],
        'exclude': [],
        'category': 'iot'
    },
    'embedded_generic': {
        'patterns': [r'embeddedos', r'embedded\s+os', r'^embedded$'],
        'exclude': [],
        'category': 'iot'
    },
    'firmware': {
        'patterns': [r'^firmware$', r'generic\s+firmware'],  # More specific - exact match or with "generic"
        'exclude': [r'embedded'],  # Don't match EmbeddedOs
        'category': 'ot'
    },
    'management_ilom': {
        'patterns': [r'ilom', r'oracle\s+ilom', r'integrated\s+lights\s+out'],
        'exclude': [],
        'category': 'management'
    },
    'embedded_axis': {
        'patterns': [r'axis\s*os', r'axisos', r'axis\s+embedded'],
        'exclude': [],
        'category': 'iot'
    },
    'thinclient_thinos_extra': {
        'patterns': [r'^thinos$'],  # Exact match for just "thinos"
        'exclude': [],
        'category': 'thinclient'
    },
    'collaboration_os': {
        'patterns': [r'collab\s*os', r'collabos', r'collaboration\s+os'],
        'exclude': [],
        'category': 'collaboration'
    },
    'unknown_ngos': {
        'patterns': [r'ng\s*os', r'ngos', r'next\s+gen\s+os'],
        'exclude': [],
        'category': 'unknown'
    },

    # ============= CONTAINER & ORCHESTRATION PLATFORMS =============
    'container_docker': {
        'patterns': [
            r'docker',
            r'docker\s+desktop',
            r'docker-ce',
            r'docker-ee',
            r'moby',
        ],
        'exclude': [],
        'category': 'container'
    },
    'container_kubernetes': {
        'patterns': [
            r'kubernetes',
            r'\bk8s\b',
            r'\bk3s\b',
            r'microk8s',
            r'azure\s+kubernetes\s+service',
            r'amazon\s+eks',
            r'\beks\b',
            r'\baks\b',
            r'\bgke\b',
        ],
        'exclude': [],
        'category': 'container'
    },
    'container_openshift': {
        'patterns': [
            r'openshift',
            r'okd',
            r'origin\s+kubernetes',
        ],
        'exclude': [],
        'category': 'container'
    },

    # ============= CLOUD & VIRTUALIZATION =============
    'cloud_chromeos': {
        'patterns': [
            r'chrome\s*os',
            r'chromium\s*os',
            r'cros',
            r'chromeos',
        ],
        'exclude': [],
        'category': 'workstation'
    },
    'cloud_photon': {
        'patterns': [
            r'photon\s*os',
            r'vmware\s+photon',
        ],
        'exclude': [],
        'category': 'container'
    },
    'virtualization_esxi': {
        'patterns': [
            r'esxi',
            r'vmware\s+esxi',
            r'vsphere',
            r'vmkernel',
        ],
        'exclude': [],
        'category': 'hypervisor'
    },

    # ============= THIN CLIENT & TERMINAL OS =============
    'thinclient_wyse': {
        'patterns': [
            r'wyse',
            r'wyse\s+thin\s*client',
            r'dell\s+wyse',
        ],
        'exclude': [],
        'category': 'thinclient'
    },
    'thinclient_thinos': {
        'patterns': [
            r'thinos',
            r'thin\s*os',
            r'dell\s+thinos',
        ],
        'exclude': [],
        'category': 'thinclient'
    },
    'thinclient_igel': {
        'patterns': [
            r'igel\s+os',
            r'igel\s+linux',
        ],
        'exclude': [],
        'category': 'thinclient'
    },

    # ============= NAS & STORAGE OS =============
    'nas_synology': {
        'patterns': [
            r'synology',
            r'synology\s*dsm',
            r'dsm\s+\d+',
        ],
        'exclude': [],
        'category': 'storage'
    },
    'nas_qnap': {
        'patterns': [
            r'qnap',
            r'qts',
            r'qutscloud',
        ],
        'exclude': [],
        'category': 'storage'
    },
    'nas_freenas': {
        'patterns': [
            r'freenas',
            r'truenas',
        ],
        'exclude': [],
        'category': 'storage'
    },

    # ============= MOBILE & CONSUMER ELECTRONICS =============
    'mobile_tizen': {
        'patterns': [
            r'tizen',
            r'samsung\s+tizen',
        ],
        'exclude': [],
        'category': 'mobile'
    },
    'mobile_webos': {
        'patterns': [
            r'webos',
            r'web\s*os',
            r'lg\s+webos',
            r'palm\s+webos',
        ],
        'exclude': [],
        'category': 'mobile'
    },
    'mobile_kaios': {
        'patterns': [
            r'kaios',
            r'kai\s*os',
        ],
        'exclude': [],
        'category': 'mobile'
    },
    'mobile_bada': {
        'patterns': [
            r'bada',
            r'samsung\s+bada',
        ],
        'exclude': [],
        'category': 'mobile'
    },

    # ============= GAMING & ENTERTAINMENT =============
    'gaming_playstation': {
        'patterns': [
            r'playstation',
            r'orbis\s*os',
            r'\bps3\b',
            r'\bps4\b',
            r'\bps5\b',
            r'sony\s+playstation',
        ],
        'exclude': [],
        'category': 'gaming'
    },
    'gaming_xbox': {
        'patterns': [
            r'xbox',
            r'xbox\s+os',
            r'durango',
            r'xbox\s+system',
        ],
        'exclude': [],
        'category': 'gaming'
    },
    'gaming_nintendo': {
        'patterns': [
            r'nintendo',
            r'switch\s*os',
            r'horizon\s*os',
            r'cafe\s*os',
            r'wii\s*u',
        ],
        'exclude': [],
        'category': 'gaming'
    },
    'media_tvos': {
        'patterns': [
            r'tvos',
            r'apple\s+tv',
            r'tv\s*os',
        ],
        'exclude': [],
        'category': 'media'
    },
    'media_watchos': {
        'patterns': [
            r'watchos',
            r'watch\s*os',
            r'apple\s+watch',
        ],
        'exclude': [],
        'category': 'wearable'
    },

    # ============= PRINTER & DEVICE OS =============
    'printer_futuresmart': {
        'patterns': [
            r'futuresmart',
            r'hp\s+futuresmart',
        ],
        'exclude': [],
        'category': 'printer'
    },
    'device_collabos': {
        'patterns': [
            r'collabos',
            r'collab\s*os',
            r'cisco\s+collaboration',
        ],
        'exclude': [],
        'category': 'collaboration'
    },

    # ============= SECURITY & FIREWALL OS =============
    'security_pfsense': {
        'patterns': [
            r'pfsense',
            r'opnsense',
        ],
        'exclude': [],
        'category': 'firewall'
    },
    'security_sophos': {
        'patterns': [
            r'sophos\s+xg',
            r'sophos\s+utm',
            r'sophos\s+firewall',
        ],
        'exclude': [],
        'category': 'firewall'
    },

    # ============= SPECIALIZED LINUX =============
    'linux_clearlinux': {
        'patterns': [
            r'clear\s*linux',
            r'clearlinux',
        ],
        'exclude': [],
        'category': 'server'
    },
    'linux_alpine': {
        'patterns': [
            r'alpine',
            r'alpine\s+linux',
        ],
        'exclude': [],
        'category': 'container'
    },
    'linux_coreos': {
        'patterns': [
            r'coreos',
            r'container\s+linux',
            r'flatcar',
            r'fedora\s+coreos',
        ],
        'exclude': [],
        'category': 'container'
    },
    'linux_rancher': {
        'patterns': [
            r'rancheros',
            r'rancher\s*os',
        ],
        'exclude': [],
        'category': 'container'
    },

    # ============= LEGACY & SPECIALIZED =============
    'legacy_beos': {
        'patterns': [
            r'beos',
            r'haiku',
        ],
        'exclude': [],
        'category': 'desktop'
    },
    'legacy_os2': {
        'patterns': [
            r'\bos/2\b',
            r'os2',
            r'arcaos',
        ],
        'exclude': [],
        'category': 'desktop'
    },
    'legacy_amiga': {
        'patterns': [
            r'amigaos',
            r'amiga\s*os',
            r'morphos',
            r'aros',
        ],
        'exclude': [],
        'category': 'desktop'
    },
}

# =============================================================================
# VERSION EXTRACTION PATTERNS
# =============================================================================
VERSION_PATTERNS = {
    'windows_build': {
        'pattern': r'(?:build\s+)?(\d{5})',
        'description': 'Windows build number (e.g., 19045)'
    },
    'windows_version': {
        'pattern': r'windows\s+(\d{1,2}(?:\.\d+)?)',
        'description': 'Windows version (e.g., 10, 11, 8.1)'
    },
    'windows_server_year': {
        'pattern': r'server\s+(20\d{2})',
        'description': 'Windows Server year (e.g., 2019, 2022)'
    },
    'linux_semantic': {
        'pattern': r'(\d+\.\d+(?:\.\d+)?)',
        'description': 'Semantic versioning (e.g., 8.8, 20.04)'
    },
    'ios_version': {
        'pattern': r'ios\s+(?:xe\s+)?(\d+\.\d+)',
        'description': 'Cisco IOS version'
    },
    'macos_version': {
        'pattern': r'(?:10\.\d{1,2}|\d{2}\.\d+)',
        'description': 'macOS version (e.g., 10.15, 13.0)'
    },
}

# =============================================================================
# EDITION/VARIANT KEYWORDS
# =============================================================================
OS_EDITIONS = {
    'windows': {
        'enterprise': 'Enterprise',
        'professional': 'Pro',
        'pro': 'Pro',
        'home': 'Home',
        'ultimate': 'Ultimate',
        'starter': 'Starter',
        'education': 'Education',
        'standard': 'Standard',
        'datacenter': 'Datacenter',
        'essentials': 'Essentials',
        'ltsc': 'LTSC',
        'ltsb': 'LTSB',
        'n': 'N',
        'kn': 'KN',
        's': 'S',
        'iot': 'IoT',
        'vdi': 'VDI',
        'azure': 'Azure Edition',
    },
    'linux': {
        'server': 'Server',
        'desktop': 'Desktop',
        'lts': 'LTS',
        'minimal': 'Minimal',
        'core': 'Core',
    },
    'macos': {
        'server': 'Server',
    },
}

# =============================================================================
# ASSET CATEGORY INFERENCE FROM OS
# =============================================================================
OS_TO_CATEGORY = {
    # High confidence mappings
    'windows_server': ('server', 0.95),
    'linux_rhel': ('server', 0.90),
    'linux_centos': ('server', 0.90),
    'linux_ubuntu': ('server', 0.85),  # Could be desktop
    'linux_debian': ('server', 0.85),
    'linux_amazon': ('server', 0.95),
    'linux_oracle': ('server', 0.90),

    'windows_desktop': ('workstation', 0.95),
    'macos': ('workstation', 0.95),

    'mobile_ios': ('mobile', 0.98),
    'mobile_android': ('mobile', 0.98),

    'network_cisco': ('network', 0.98),
    'network_juniper': ('network', 0.98),
    'network_arista': ('network', 0.98),

    'windows_embedded': ('iot', 0.85),
    'firmware': ('ot', 0.75),

    'virtualization_vmware': ('server', 0.90),
    'bsd': ('server', 0.80),
}

# =============================================================================
# COMMON OS NAME VARIATIONS (for fuzzy matching)
# =============================================================================
OS_NAME_VARIATIONS = {
    # Windows variations
    'windows': ['win', 'mswindows', 'ms-windows', 'microsoft windows'],
    'windows 10': ['win10', 'windows10', 'win 10'],
    'windows 11': ['win11', 'windows11', 'win 11'],
    'windows server': ['winserver', 'win server', 'ms server'],

    # Linux variations
    'rhel': ['red hat', 'redhat', 'red hat enterprise linux', 'red hat linux'],
    'centos': ['cent os'],
    'ubuntu': ['ubuntu linux'],

    # macOS variations
    'macos': ['mac os', 'mac os x', 'osx', 'os x', 'darwin', 'macmdm'],

    # Mobile variations
    'ios': ['iphone os', 'apple ios', 'ipados'],
    'android': ['google android'],

    # Network variations
    'cisco ios': ['ios', 'cisco', 'ios software'],
    'junos': ['juniper junos', 'junos os'],
}

# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def get_cpe_vendor(vendor_string: str) -> str:
    """Convert vendor string to CPE format"""
    return CPE_VENDOR_MAP.get(vendor_string.lower(), vendor_string.lower())

def get_cpe_product(product_string: str) -> str:
    """Convert product string to CPE format"""
    return CPE_PRODUCT_MAP.get(product_string.lower(), product_string.lower().replace(' ', '_'))

def infer_category_from_family(os_family: str) -> tuple:
    """
    Infer asset category from OS family
    Returns: (category, confidence)
    """
    return OS_TO_CATEGORY.get(os_family, ('unknown', 0.0))

if __name__ == '__main__':
    print("OS Knowledge Base Loaded")
    print(f"IANA OS Names: {len(IANA_OS_NAMES)}")
    print(f"CPE Vendor Mappings: {len(CPE_VENDOR_MAP)}")
    print(f"CPE Product Mappings: {len(CPE_PRODUCT_MAP)}")
    print(f"OS Family Patterns: {len(OS_FAMILY_PATTERNS)}")
    print(f"Version Patterns: {len(VERSION_PATTERNS)}")
