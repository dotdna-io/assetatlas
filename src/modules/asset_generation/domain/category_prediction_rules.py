#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Pattern-based category and subcategory predictor for AssetAtlas.
This version uses intelligent patterns instead of hardcoded values for flexibility.

Author: AssetAtlas Team
Date: 2025-10-29
"""

import functools
import re
from typing import Tuple, Optional, Dict, Any


class PatternBasedCategoryPredictor:
    """
    Pattern-based category predictor using intelligent matching instead of hardcoded values.
    Returns (category, subcategory, confidence) or None if no rule matches.
    """

    def __init__(self):
        # Pre-compile whitespace regex for _normalize_string
        self._whitespace_re = re.compile(r'\s+')

        # Initialize pattern-based rules
        self._init_os_patterns()
        self._init_manufacturer_patterns()
        self._init_model_patterns()
        self._init_combined_patterns()
        self._init_hostname_patterns()

        # Pre-compile all pattern lists
        self.os_patterns = self._compile_patterns(self.os_patterns)
        self.manufacturer_patterns = self._compile_patterns(self.manufacturer_patterns)
        self.model_patterns = self._compile_patterns(self.model_patterns)
        self.hostname_patterns = self._compile_patterns(self.hostname_patterns)

        # Pre-compile combined patterns (two regex columns per tuple)
        self.combined_patterns = [
            (re.compile(os_pat), re.compile(mfr_pat), cat, subcat, conf)
            for os_pat, mfr_pat, cat, subcat, conf in self.combined_patterns
        ]

        # Pre-compile manufacturer normalizations
        self.manufacturer_normalizations = [
            (re.compile(pattern), replacement)
            for pattern, replacement in self.manufacturer_normalizations
        ]

        # Pre-compile inline patterns used in _check_combined_patterns
        self._combined_inline = {
            'vm_model': re.compile(r'\bvmware\d+,\d+\b|virtual\s+machine'),
            'server_os': re.compile(r'windows\s+server|linux|ubuntu|rhel|centos|debian|sles'),
            'windows_client_os': re.compile(r'windows\s+\d|windows\s*$'),
            'hypervisor_os': re.compile(r'vmware|esxi'),
            'hypervisor_os_full': re.compile(r'vmware|esxi|hyper-v|proxmox|xen'),
            'cisco_mfr': re.compile(r'^cisco'),
            'cisco_firewall_model': re.compile(r'\bfirewall\b|\basa\b'),
            'cisco_router_model': re.compile(r'\brouter\b'),
            'cisco_switch_model': re.compile(r'\bswitch\b|\bcatalyst\b|\bnexus\b'),
            'cisco_hypervisor_model': re.compile(r'\bhypervisor\b|\besxi\b'),
            'dell_mfr': re.compile(r'^dell'),
            'dell_server_model': re.compile(r'\bserver\b|\bpoweredge\b'),
            'dell_laptop_model': re.compile(r'\blaptop\b|\blatitude\b|\bxps\b|\binspir'),
            'dell_desktop_model': re.compile(r'\bdesktop\b|\boptiplex\b'),
            'dell_precision_model': re.compile(r'\bprecision\b'),
            'precision_tower_model': re.compile(r'\btower\b|\bsff\b|\bmicro\b|\b\d{4}\b'),
            'precision_mobile_model': re.compile(r'\bmobile\b'),
            'lenovo_mfr': re.compile(r'^lenovo'),
            'lenovo_server_model': re.compile(r'\bserver\b|\bthinkagile\b|\bthinksystem\b'),
            'lenovo_thinkpad_model': re.compile(r'\bthinkpad\b'),
            'lenovo_desktop_model': re.compile(r'\bthinkcentre\b|\bideacentre\b'),
            'lenovo_laptop_machinetype': re.compile(r'^(?:20|21)[a-z0-9]{2}'),  # (#280)
            'lenovo_desktop_machinetype': re.compile(r'^(?:10|11|30)[a-z0-9]{2}'),  # (#280)
            'hp_mfr': re.compile(r'^hp\b|^hewlett'),
            'hp_server_model': re.compile(r'\bserver\b|\bproliant\b'),
            'hp_laptop_model': re.compile(r'\blaptop\b|\belitebook\b|\bpavilion\b|\benvy\b'),
            'hp_pro_desktop_model': re.compile(r'\bpro\s+(?:sff|mini|tower)\b|\bprodesk\b|\bpro\s+\d+\s+g\d|\belitedesk\b|\beliteone\b'),
            'hp_z_desktop_model': re.compile(r'\bz[0-9]+\s+(?:mini|tower|g\d)\b'),
            'hp_printer_model': re.compile(r'\bprinter\b|\blaserjet\b|\bdeskjet\b|\bofficejet\b'),
            'apple_mfr': re.compile(r'^apple'),
            'apple_macbook_model': re.compile(r'\bmacbook'),
            'apple_desktop_model': re.compile(r'\bimac|\bmac\s*mini|\bmac\s*studio|\bmac\s*pro(?!.*book)'),
            'apple_ipad_model': re.compile(r'\bipad\b'),
            'apple_iphone_model': re.compile(r'\biphone\b'),
            'apple_generic_mac_id': re.compile(r'\bmac\d+,\d+'),  # Mac14,2 — generic Apple Silicon (#59 Item 17)
            'samsung_mfr': re.compile(r'^samsung'),
            'samsung_tablet_model': re.compile(r'\bsm-[tx]\d+|\bgalaxy\s+tab\b'),
            'samsung_phone_model': re.compile(r'\bsm-[sgaf]\d+|\bgalaxy\s+(?:s|z|a|note)\b'),
        }

        # Pre-compile inline patterns used in _check_os_patterns
        self._os_inline = {
            'embedded_fallback': re.compile(r'\bembedded\b|\brtos\b|\bfirmware\b'),
        }

    def _compile_patterns(self, patterns):
        """Pre-compile regex patterns for performance."""
        return [
            (re.compile(pattern), *rest)
            for pattern, *rest in patterns
        ]

    def _init_os_patterns(self):
        """
        Initialize OS-based category prediction patterns.

        ORDERING RULES:
        - Specific distributions/products MUST come before generic patterns
        - Example: 'ubuntu_desktop' before 'linux_generic'
        - Network/infrastructure OS patterns before general server patterns
        - Mobile/embedded OS patterns are separate and don't overlap

        Pattern priority within each section is top-to-bottom (first match wins).
        """
        # Each pattern is (regex_pattern, category, subcategory, confidence)
        self.os_patterns = [
            # Server OS patterns
            (r'^sles\s*\d*', 'server', 'server', 1.0),  # SLES, SLES 15, SLES 12, etc.
            (r'^ubuntu\s+server', 'server', 'server', 1.0),  # Ubuntu Server (any version)
            (r'^red\s*hat\s+enterprise\s+linux\s+server', 'server', 'server', 1.0),
            (r'^rhel\s+.*server', 'server', 'server', 1.0),
            (r'^centos\s+.*server', 'server', 'server', 1.0),
            (r'^centos\b', 'server', 'server', 0.95),  # CentOS without "server" suffix (#59 Item 8)
            (r'^debian\s+.*server', 'server', 'server', 1.0),
            (r'^amazon\s+linux', 'server', 'server', 0.95),  # Amazon Linux 2023, etc. (#59 Item 8)
            (r'^freebsd', 'server', 'server', 0.95),  # FreeBSD (#59 Item 8)
            (r'^aix', 'server', 'server', 0.95),  # IBM AIX (#59 Item 8)
            (r'^windows\s+server\s+\d{4}', 'server', 'server', 0.95),  # Windows Server 2019, 2022, etc.
            (r'^windows\s+20\d{2}\b', 'server', 'server', 0.95),  # Windows 2019, 2016, 2008 without "Server" (#59 Item 8)
            (r'^windows\s+server', 'server', 'server', 0.95),

            # Linux desktop OS patterns — categorized as 'server' because in enterprise
            # environments, Linux desktops are rare; the vast majority of Linux hosts are
            # servers, containers, or appliances. Treating them as 'server' by default
            # produces more accurate results across the fleet.
            (r'^ubuntu\s+\d{2}\.\d{2}(?!\s+server)', 'server', 'server', 0.95),  # Ubuntu 22.04, but not server
            (r'^ubuntu\s+(?!server|core)', 'server', 'server', 0.95),  # Ubuntu desktop
            (r'^fedora\s+\d+', 'server', 'server', 0.95),
            (r'^debian\s+(?!.*server)', 'server', 'server', 0.95),
            (r'^arch\s+linux', 'server', 'server', 0.95),
            (r'^manjaro', 'server', 'server', 0.95),
            (r'^mint\s+\d+', 'server', 'server', 0.95),
            (r'^pop!?_?os', 'server', 'server', 0.95),
            (r'^rhel\s+\d+(?!\s+server)', 'server', 'server', 0.95),  # RHEL desktop

            # Hypervisor patterns
            (r'^vmware\s+esxi', 'server', 'hypervisor', 1.0),  # VMware ESXi (any version)
            (r'^esxi', 'server', 'hypervisor', 1.0),  # ESXi without vmware prefix (#59 Item 8)
            (r'^hyper-v\s+\d{4}', 'server', 'hypervisor', 1.0),  # Hyper-V 2019, 2022, etc.
            (r'^hyper-v', 'server', 'hypervisor', 1.0),
            (r'^xenserver', 'server', 'hypervisor', 1.0),
            (r'^xen\s+\d', 'server', 'hypervisor', 1.0),
            (r'^proxmox\s+ve', 'server', 'hypervisor', 1.0),
            (r'^kvm', 'server', 'hypervisor', 0.95),

            # Workstation OS patterns — subcategory 'unknown' because OS alone can't
            # distinguish laptop vs desktop; combined/model patterns refine it (#58 Item 2)
            (r'^windows\s+1[01]', 'workstation', 'unknown', 1.0),  # Windows 10, 11
            (r'^windows\s+\d+\s+(?!server)', 'workstation', 'unknown', 0.95),  # Windows with version, not server
            (r'^macos', 'workstation', 'unknown', 1.0),
            (r'^mac\s+os\s+x', 'workstation', 'unknown', 1.0),
            (r'^os\s+x\s+\d+', 'workstation', 'unknown', 1.0),

            # Network OS patterns
            (r'^cisco\s+ios(?![\s-]?x)', 'network', 'switch', 1.0),  # Cisco IOS (not IOS-XE/XR)
            (r'^(?:cisco\s+)?ios[\s-]?xe', 'network', 'switch', 1.0),  # IOS XE, IOS-XE, ios xe
            (r'^(?:cisco\s+)?ios[\s-]?xr', 'network', 'router', 1.0),  # IOS XR, IOS-XR, ios xr
            (r'^nx-os', 'network', 'switch', 1.0),
            (r'^nxos', 'network', 'switch', 1.0),
            (r'^junos', 'network', 'switch', 1.0),
            (r'^juniper', 'network', 'switch', 0.95),
            (r'^aruba\s*os', 'network', 'switch', 1.0),
            (r'^aos-cx', 'network', 'switch', 1.0),  # Aruba AOS-CX
            (r'^fortios', 'network', 'firewall', 1.0),
            (r'^forti\s*os', 'network', 'firewall', 1.0),
            (r'^panos', 'network', 'firewall', 1.0),
            (r'^pan-os', 'network', 'firewall', 1.0),
            (r'^asa\s+\d+', 'network', 'firewall', 1.0),  # ASA 9.18, etc.
            (r'^pfsense', 'network', 'firewall', 1.0),
            (r'^opnsense', 'network', 'firewall', 1.0),
            (r'^vyos', 'network', 'router', 1.0),
            (r'^mikrotik', 'network', 'router', 0.95),
            (r'^router\s*os', 'network', 'router', 1.0),
            (r'^dnos', 'network', 'switch', 1.0),
            (r'^dell\s+networking\s+os', 'network', 'switch', 1.0),
            # Additional network vendor OS patterns
            (r'^tmos', 'network', 'load_balancer', 1.0),  # F5 TMOS
            (r'^f5\s+tmos', 'network', 'load_balancer', 1.0),
            (r'^loadmaster', 'network', 'load_balancer', 1.0),  # Kemp LoadMaster
            (r'^lmos', 'network', 'load_balancer', 1.0),  # Kemp LMOS
            (r'^sfos', 'network', 'firewall', 1.0),  # Sophos SFOS
            (r'^sophos\s+(?:firewall\s+)?os', 'network', 'firewall', 1.0),
            (r'^sophos\s+utm', 'network', 'firewall', 1.0),
            (r'^sonicos', 'network', 'firewall', 1.0),  # SonicWall SonicOS
            (r'^sonicwall', 'network', 'firewall', 1.0),  # SonicWall without SonicOS (#59 Item 8)
            (r'^netscaler', 'network', 'load_balancer', 1.0),  # Citrix NetScaler (#59 Item 8)
            (r'^raptor', 'network', 'firewall', 0.9),  # Raptor firewall (#59 Item 8)
            (r'^gaia\s+os', 'network', 'firewall', 1.0),  # Check Point Gaia
            (r'^check\s*point\s+gaia', 'network', 'firewall', 1.0),
            (r'^splat', 'network', 'firewall', 1.0),  # Check Point SPLAT
            (r'^akamai', 'network', 'cdn', 0.9),  # Akamai
            (r'^meraki\s+cloud', 'network', 'switch', 0.95),  # Cisco Meraki
            (r'^meraki', 'network', 'switch', 0.95),
            (r'^comware', 'network', 'switch', 1.0),  # HPE Comware
            (r'^hpe?\s+comware', 'network', 'switch', 1.0),

            # Mobile OS patterns
            (r'^ios\s+1[4-9]', 'mobile', 'phone', 1.0),  # iOS 14+
            (r'^ios\s+\d+', 'mobile', 'phone', 0.95),  # Any iOS version
            (r'^ipados', 'mobile', 'tablet', 1.0),
            (r'^android\s+\d+', 'mobile', 'phone', 1.0),
            (r'^android', 'mobile', 'phone', 0.95),
            (r'^samsung\s+one\s+ui', 'mobile', 'phone', 1.0),
            (r'^oxygen\s*os', 'mobile', 'phone', 1.0),
            (r'^color\s*os', 'mobile', 'phone', 1.0),
            (r'^miui', 'mobile', 'phone', 1.0),
            (r'^harmony\s*os', 'mobile', 'phone', 0.95),

            # OT OS patterns
            (r'^rslogix', 'ot', 'plc', 1.0),
            (r'^studio\s+5000', 'ot', 'plc', 1.0),
            (r'^tia\s+portal', 'ot', 'plc', 1.0),
            (r'^step\s+7', 'ot', 'plc', 1.0),
            (r'^wincc', 'ot', 'hmi', 1.0),
            (r'^wonderware', 'ot', 'hmi', 0.95),
            (r'^factorytalk', 'ot', 'hmi', 0.95),
            (r'^vxworks', 'ot', 'controller', 1.0),
            (r'^qnx', 'ot', 'controller', 1.0),
            (r'^rtos', 'ot', 'controller', 0.9),
            (r'^codesys', 'ot', 'plc', 0.95),
            # Additional OT vendor patterns
            (r'^controllogix', 'ot', 'plc', 1.0),  # Rockwell ControlLogix
            (r'^compactlogix', 'ot', 'plc', 1.0),  # Rockwell CompactLogix
            (r'^os-9', 'ot', 'controller', 1.0),  # Allen-Bradley OS-9
            (r'^microware\s+os-9', 'ot', 'controller', 1.0),
            (r'^compact\s+hmi', 'ot', 'hmi', 1.0),  # ABB Compact HMI
            (r'^system\s+800xa', 'ot', 'dcs', 1.0),  # ABB System 800xA
            (r'^webaccess', 'ot', 'scada', 0.95),  # Advantech WebAccess
            (r'^wise-paas', 'iot', 'gateway', 0.95),  # Advantech WISE-PaaS
            (r'^impact\s+iot', 'iot', 'gateway', 0.95),  # Nokia IMPACT IoT
            (r'^mx\s+industrial\s+edge', 'ot', 'gateway', 1.0),  # Nokia MXIE
            (r'^mxie', 'ot', 'gateway', 1.0),  # Nokia MXIE
            (r'^edr[\s-]firmware', 'network', 'industrial_router', 0.95),  # Moxa EDR firmware

            # IoT OS patterns
            (r'^ubuntu\s+core', 'iot', 'embedded_pc', 1.0),
            (r'^windows\s+iot', 'iot', 'embedded_pc', 1.0),
            (r'^raspbian', 'iot', 'embedded_pc', 1.0),
            (r'^raspberry\s+pi\s+os', 'iot', 'embedded_pc', 1.0),
            (r'^armbian', 'iot', 'embedded_pc', 0.95),
            (r'^mbed\s+os', 'iot', 'embedded_pc', 1.0),
            (r'^free\s*rtos', 'iot', 'embedded_pc', 1.0),
            (r'^amazon\s+freertos', 'iot', 'embedded_pc', 1.0),
            (r'^azure\s+sphere', 'iot', 'smart_home', 1.0),
            (r'^esp\s*home', 'iot', 'embedded_pc', 1.0),
            (r'^tasmota', 'iot', 'smart_home', 0.95),
            (r'^openwrt', 'iot', 'gateway', 0.95),
            (r'^dd-wrt', 'iot', 'gateway', 0.9),
            # Smart TV / consumer device OS patterns (#26 Issue 6)
            (r'^tizen', 'iot', 'smart_tv', 1.0),  # Samsung Smart TVs
            (r'^(?:lg\s+)?web\s*os', 'iot', 'smart_tv', 1.0),  # LG Smart TVs
            (r'^roku\s*os', 'iot', 'smart_tv', 1.0),  # Roku devices
            (r'^smartcast\s*os', 'iot', 'smart_tv', 1.0),  # Vizio Smart TVs
            (r'^(?:cisco\s+)?room\s*os', 'iot', 'video_conference', 1.0),  # Cisco room devices
            (r'^(?:amazon\s+)?fire\s*os', 'iot', 'smart_tv', 1.0),  # Amazon Fire devices
            (r'^axis\s*os', 'iot', 'ip_camera', 1.0),  # Axis security cameras
            (r'^synology\s*dsm', 'iot', 'nas', 1.0),  # Synology NAS

            # Server management/BMC OS patterns (#26 Issue 6)
            (r'^(?:oracle\s+)?ilom', 'server', 'management', 1.0),  # Oracle ILOM
            (r'^(?:dell\s+)?idrac', 'server', 'management', 1.0),  # Dell iDRAC
            (r'^(?:hpe?\s+)?ilo', 'server', 'management', 1.0),  # HP/HPE iLO

            # Enterprise Linux variants (#26 Issue 1, Fix D)
            (r'^enterprise\s+linux', 'server', 'server', 0.95),

            # Bare Linux kernel versions — server in enterprise context (#59 Item 8)
            (r'^linux\s+\d', 'server', 'server', 0.9),  # linux 2.6, linux 5.4.0, etc.

            # Cisco IOS version strings without product name (#59 Item 8)
            (r'^\d+\.\d+\(', 'network', 'switch', 0.85),  # 12.2(44)SE5, 15.1(4)M

            # Generic OS fallbacks — MUST be last in list (#26 Issue 1, Fixes A/C/E)
            (r'^ios$', 'mobile', 'phone', 0.9),  # Apple iOS (bare, no version)
            (r'^windows$', 'workstation', 'unknown', 0.5),  # Generic Windows, low confidence
            (r'^linux$', 'server', 'server', 0.5),  # Generic Linux, low confidence

            # Printer OS patterns
            (r'printer\s+firmware', 'miscellaneous', 'printer', 1.0),
            (r'hp\s+futuresmart', 'miscellaneous', 'printer', 1.0),
            (r'vendor\s+firmware', 'miscellaneous', 'printer', 0.9),
            (r'embedded\s+web\s+server.*print', 'miscellaneous', 'printer', 0.95),
            (r'.*\bprinter\b.*firmware', 'miscellaneous', 'printer', 0.95),
            # Canon printer OS patterns
            (r'^meap', 'miscellaneous', 'printer', 1.0),  # Canon MEAP
            (r'^canon\s+meap', 'miscellaneous', 'printer', 1.0),
            (r'^imagerunner', 'miscellaneous', 'printer', 1.0),  # Canon imageRUNNER
            # Xerox printer OS patterns
            (r'^connectkey', 'miscellaneous', 'printer', 1.0),  # Xerox ConnectKey
            (r'^xerox\s+connectkey', 'miscellaneous', 'printer', 1.0),
            (r'^altalink', 'miscellaneous', 'printer', 1.0),  # Xerox AltaLink
            (r'^versalink', 'miscellaneous', 'printer', 1.0),  # Xerox VersaLink
        ]

    def _init_manufacturer_patterns(self):
        """Initialize manufacturer pattern-based rules"""
        # Patterns for normalizing manufacturer names
        self.manufacturer_normalizations = [
            (r'\s+(inc\.?|incorporated|corporation|corp\.?|company|co\.?)\s*$', ''),
            (r'\s+(limited|ltd\.?|llc|l\.l\.c\.?|gmbh|ag|sa|spa|srl)\s*$', ''),
            (r'\s+(technologies|technology|tech|systems|system)\s*$', ''),
            (r'\s+(global|international|group)\s*$', ''),
            (r'[,\.]', ''),
            (r'\s+', ' '),
        ]

        # Manufacturer patterns (after normalization)
        self.manufacturer_patterns = [
            # Server manufacturers
            (r'^ibm', 'server', 'server', 1.0),
            (r'^super\s*micro', 'server', 'server', 1.0),
            (r'^supermicro', 'server', 'server', 1.0),
            (r'^dell\s+emc', 'server', 'server', 1.0),
            (r'^oracle', 'server', 'server', 0.9),

            # Network manufacturers
            (r'^arista', 'network', 'switch', 1.0),
            (r'^netgear', 'network', 'switch', 0.95),
            (r'^extreme', 'network', 'switch', 1.0),
            (r'^ubiquiti', 'network', 'switch', 0.95),
            (r'^fortinet', 'network', 'firewall', 0.95),
            (r'^palo\s*alto', 'network', 'firewall', 0.95),
            (r'^checkpoint', 'network', 'firewall', 1.0),
            (r'^sonicwall', 'network', 'firewall', 1.0),
            (r'^watchguard', 'network', 'firewall', 1.0),
            (r'^f5', 'network', 'load_balancer', 0.95),

            # Mobile manufacturers
            (r'^apple', 'mobile', 'phone', 0.8),  # Could also be workstation
            (r'^samsung', 'mobile', 'phone', 0.85),
            (r'^google', 'mobile', 'phone', 0.7),  # Could be various
            (r'^motorola', 'mobile', 'phone', 1.0),
            (r'^nokia', 'mobile', 'phone', 0.95),
            (r'^sony', 'mobile', 'phone', 0.8),
            (r'^lg', 'mobile', 'phone', 0.85),
            (r'^htc', 'mobile', 'phone', 1.0),
            (r'^oneplus', 'mobile', 'phone', 1.0),
            (r'^xiaomi', 'mobile', 'phone', 0.95),
            (r'^oppo', 'mobile', 'phone', 0.95),
            (r'^vivo', 'mobile', 'phone', 0.95),
            (r'^realme', 'mobile', 'phone', 0.95),
            (r'^sonim', 'mobile', 'phone', 1.0),
            (r'^cat\b', 'mobile', 'phone', 0.95),  # CAT phones
            (r'^caterpillar', 'mobile', 'phone', 0.9),

            # Printer manufacturers (HP removed — too broad, catches monitors/workstations #59 Item 14)
            (r'^canon', 'miscellaneous', 'printer', 1.0),
            (r'^epson', 'miscellaneous', 'printer', 1.0),
            (r'^brother', 'miscellaneous', 'printer', 1.0),
            (r'^xerox', 'miscellaneous', 'printer', 1.0),
            (r'^lexmark', 'miscellaneous', 'printer', 1.0),
            (r'^ricoh', 'miscellaneous', 'printer', 0.95),
            (r'^konica\s*minolta', 'miscellaneous', 'printer', 1.0),
            (r'^dymo', 'miscellaneous', 'label_printer', 1.0),

            # IoT manufacturers (predict only category, not subcategory)
            (r'^raspberry\s*pi', 'iot', None, 1.0),
            (r'^arduino', 'iot', None, 1.0),
            (r'^espressif', 'iot', None, 1.0),
            (r'^particle', 'iot', None, 1.0),
            (r'^beagle', 'iot', None, 0.95),
            (r'^pine64', 'iot', None, 0.95),
            (r'^hid\s*global', 'iot', None, 1.0),
            (r'^hid\s*corp', 'iot', None, 1.0),
            (r'^amazon', 'iot', None, 0.7),  # Could be various
            (r'^nest', 'iot', None, 1.0),
            (r'^philips\s*hue', 'iot', None, 1.0),
            (r'^signify', 'iot', None, 0.95),

            # OT manufacturers (predict only category, not subcategory)
            (r'^rockwell', 'ot', None, 1.0),
            (r'^allen[\s-]*bradley', 'ot', None, 1.0),
            (r'^siemens', 'ot', None, 0.85),  # Makes many things
            (r'^schneider', 'ot', None, 0.85),
            (r'^abb\b', 'ot', None, 0.85),
            (r'^honeywell', 'ot', None, 0.8),
            (r'^emerson', 'ot', None, 0.85),
            (r'^omron', 'ot', None, 0.95),
            (r'^mitsubishi\s*electric', 'ot', None, 0.9),
            (r'^yokogawa', 'ot', None, 0.95),
            (r'^phoenix\s*contact', 'ot', None, 0.95),

            # Miscellaneous manufacturers
            (r'^apc\b', 'miscellaneous', 'ups', 1.0),
            (r'^eaton', 'miscellaneous', 'ups', 0.95),
            (r'^cyberpower', 'miscellaneous', 'ups', 1.0),
            (r'^tripp[\s-]*lite', 'miscellaneous', 'ups', 1.0),
            (r'^logitech', 'miscellaneous', 'video_conference', 0.95),
            (r'^poly\b', 'miscellaneous', 'video_conference', 0.95),
            (r'^barco', 'miscellaneous', 'video_conference', 0.95),
            (r'^crestron', 'miscellaneous', 'video_conference', 0.9),

            # Workstation manufacturers (special cases)
            (r'^microsoft', 'workstation', 'laptop', 0.9),
            (r'^surface', 'workstation', 'laptop', 1.0),
        ]

    def _init_model_patterns(self):
        """Initialize model pattern-based rules"""
        # Model keyword patterns with priority order
        self.model_patterns = [
            # Multi-word patterns (higher priority)
            # VM model strings (#58 Item 4) — without OS context, default to virtual_desktop
            (r'\bvmware\d+,\d+\b', 'workstation', 'virtual_desktop', 0.8),
            (r'\bvirtual\s+machine\b', 'workstation', 'virtual_desktop', 0.8),
            (r'\bvirtual\s+desktop\b', 'workstation', 'virtual_desktop', 1.0),
            (r'\bthin\s+client\b', 'workstation', 'thin_client', 1.0),
            (r'\bblade\s+server\b', 'server', 'server', 1.0),
            (r'\brack\s+server\b', 'server', 'server', 1.0),
            (r'\btower\s+server\b', 'server', 'server', 1.0),
            (r'\bnetwork\s+storage\b', 'server', 'network_storage', 1.0),
            (r'\b(?:nas|san)\s+storage\b', 'server', 'network_storage', 1.0),
            (r'\bcontainer\s+host\b', 'server', 'server', 1.0),
            (r'\bwireless\s+access\s+point\b', 'network', 'wireless_access_point', 1.0),
            (r'\baccess\s+point\b', 'network', 'wireless_access_point', 1.0),
            (r'\bwap\b', 'network', 'wireless_access_point', 0.95),
            (r'\bload\s+balancer\b', 'network', 'load_balancer', 1.0),
            (r'\bindustrial\s+router\b', 'network', 'industrial_router', 1.0),
            (r'\bindustrial\s+switch\b', 'network', 'industrial_switch', 1.0),
            (r'\brugged\s+phone\b', 'mobile', 'phone', 1.0),
            (r'\bindustrial\s+printer\b', 'miscellaneous', 'industrial_printer', 1.0),
            (r'\b3d\s+printer\b', 'miscellaneous', '3d_printer', 1.0),
            (r'\bmultifunction\b|\bmfp\b', 'miscellaneous', 'multifunction', 1.0),
            (r'\ball[\s-]in[\s-]one\b', 'miscellaneous', 'multifunction', 0.95),
            (r'\bbarcode\s+(?:reader|scanner)\b', 'miscellaneous', 'barcode_reader', 1.0),
            (r'\bbadge\s+reader\b', 'miscellaneous', 'badge_reader', 1.0),
            (r'\bvideo\s+conference\b', 'miscellaneous', 'video_conference', 1.0),
            # Poly/Polycom VoIP desk phones and conference devices (#58 Item 1)
            (r'\bpoly\s+c[0-9]{3}', 'miscellaneous', 'voip', 1.0),   # Poly C435HD, C455HD
            (r'\bpoly\s+ccx\d+', 'miscellaneous', 'voip', 1.0),      # Poly CCX500
            (r'\bpoly\s+mp\d+', 'miscellaneous', 'voip', 1.0),       # Poly MP56
            (r'\bpoly\s+tc\d+', 'miscellaneous', 'voip', 1.0),       # Poly TC8
            (r'\bpolycom\b', 'miscellaneous', 'voip', 0.95),         # Generic Polycom
            (r'\bip\s+camera\b', 'iot', 'ip_camera', 1.0),
            (r'\bsmart\s+home\b', 'iot', 'smart_home', 1.0),
            (r'\bembedded\s+pc\b', 'iot', 'embedded_pc', 1.0),
            (r'\baccess\s+control\b', 'iot', 'access_control', 1.0),
            (r'\bac\s+drive[s]?\b', 'ot', 'ac_drives', 1.0),
            (r'\bvariable\s+frequency\s+drive\b|\bvfd\b', 'ot', 'ac_drives', 1.0),
            (r'\bpowerflex\b', 'ot', 'ac_drives', 0.95),

            # Single word/simple patterns (lower priority)
            (r'\blaptop\b', 'workstation', 'laptop', 1.0),
            (r'\bnotebook\b', 'workstation', 'laptop', 0.95),
            (r'\bultrabook\b', 'workstation', 'laptop', 1.0),
            (r'\bchromebook\b', 'workstation', 'laptop', 1.0),
            (r'\bworkstation\b', 'workstation', 'workstation', 1.0),
            (r'\bdesktop\b', 'workstation', 'desktop', 0.95),
            (r'\bserver\b', 'server', 'server', 0.95),
            (r'\bhypervisor\b', 'server', 'hypervisor', 1.0),
            (r'\bfirewall\b', 'network', 'firewall', 1.0),
            (r'\brouter\b', 'network', 'router', 0.95),
            (r'\bswitch\b', 'network', 'switch', 0.9),
            (r'\btablet\b', 'mobile', 'tablet', 1.0),
            (r'\bipad\b', 'mobile', 'tablet', 1.0),
            (r'\bsm-[tx]\d+', 'mobile', 'tablet', 1.0),  # Samsung tablet model prefixes (#58 Item 5)
            (r'\bgalaxy\s+tab\b', 'mobile', 'tablet', 1.0),  # Samsung Galaxy Tab (#58 Item 5)
            (r'\bphone\b', 'mobile', 'phone', 0.95),
            (r'\biphone\b', 'mobile', 'phone', 1.0),
            (r'\bsmartphone\b', 'mobile', 'smartphone', 1.0),
            (r'\bprinter\b', 'miscellaneous', 'printer', 0.95),
            (r'\bscanner\b', 'miscellaneous', 'scanner', 0.95),
            (r'\bplotter\b', 'miscellaneous', 'plotter', 1.0),
            (r'\bcopier\b', 'miscellaneous', 'multifunction', 0.9),
            (r'\bsensor\b', 'iot', 'sensor', 0.9),
            (r'\bhmi\b', 'ot', 'hmi', 1.0),
            (r'\bplc\b', 'ot', 'plc', 1.0),
            (r'\bdcs\b', 'ot', 'dcs', 1.0),
            (r'\brtu\b', 'ot', 'rtu', 1.0),
            (r'\bscada\b', 'ot', 'scada', 1.0),
            (r'\bcontroller\b', 'ot', 'controller', 0.85),
            (r'\bups\b', 'miscellaneous', 'ups', 1.0),
            (r'\bpdu\b', 'miscellaneous', 'pdu', 1.0),
            (r'\bkvm\b', 'miscellaneous', 'kvm', 0.95),

            # Monitor model patterns (#59 Item 3)
            # Confidence >= 0.9 so they match at priority 3 (before manufacturer patterns)
            (r'\bmonitor\b', 'miscellaneous', 'monitor', 1.0),
            (r'\belitedisplay\b', 'miscellaneous', 'monitor', 1.0),      # HP EliteDisplay
            (r'\bz\d+[a-z]?\s+g\d\b', 'miscellaneous', 'monitor', 0.9),  # HP Z24 G3, Z27 G3
            (r'\be\d{2,3}\s+g\d\b', 'miscellaneous', 'monitor', 0.9),   # HP E24 G5, E27 G4
            (r'\be\d{3}\b', 'miscellaneous', 'monitor', 0.9),            # HP E233, E232, E231
            (r'\bultrawide\b', 'miscellaneous', 'monitor', 1.0),         # LG ULTRAWIDE
            (r'\bultrasharp\b', 'miscellaneous', 'monitor', 1.0),        # Dell UltraSharp
            (r'\blf\d{2}[a-z]\d+[a-z]*\b', 'miscellaneous', 'monitor', 0.9),  # Samsung LF24T450F, LF27T370
            (r'\b[csl]\d{2}[a-z]\d{2,3}[a-z]*\b', 'miscellaneous', 'monitor', 0.9),  # Samsung C27F390, S24E450

            # HP printer model patterns (#59 Item 14 — replace blanket HP→printer rule)
            (r'\blaserjet\b', 'miscellaneous', 'printer', 1.0),
            (r'\bdeskjet\b', 'miscellaneous', 'printer', 1.0),
            (r'\bofficejet\b', 'miscellaneous', 'printer', 1.0),
            (r'\bpagewide\b', 'miscellaneous', 'printer', 1.0),
            (r'\bdesignjet\b', 'miscellaneous', 'printer', 1.0),
            (r'\bneverstop\b', 'miscellaneous', 'printer', 1.0),
            (r'\bsprocket\b', 'miscellaneous', 'printer', 1.0),
            (r'\benvy\s+\d+\s*pro\b', 'miscellaneous', 'printer', 0.9),  # HP ENVY 6020 Pro etc.

            # Model number patterns
            (r'poweredge\s+[rtm]\d+', 'server', 'server', 1.0),  # Dell servers
            (r'proliant\s+[dgm]l\d+', 'server', 'server', 1.0),  # HP servers
            (r'thinkpad\s+[txpel]\d+', 'workstation', 'laptop', 1.0),  # Lenovo laptops
            (r'thinkcentre\b', 'workstation', 'desktop', 1.0),  # Lenovo desktops (#58 Item 2)
            (r'elitebook\s+\d+', 'workstation', 'laptop', 1.0),  # HP laptops
            (r'latitude\s+\d+', 'workstation', 'laptop', 1.0),  # Dell laptops
            (r'optiplex\s+\d+', 'workstation', 'desktop', 1.0),  # Dell desktops
            (r'\bimac', 'workstation', 'desktop', 1.0),  # Apple iMac / iMac21,1 (#59 Item 17)
            (r'\bmac\s*mini', 'workstation', 'desktop', 1.0),  # Apple Mac mini / Macmini9,1 (#59 Item 17)
            (r'\bmac\s*studio', 'workstation', 'desktop', 1.0),  # Apple Mac Studio (#59 Item 17)
            (r'\bmac\s*pro\d', 'workstation', 'desktop', 1.0),  # Apple Mac Pro / MacPro7,1 (#59 Item 17)
            (r'\bmacbook', 'workstation', 'laptop', 1.0),  # Apple MacBook / MacBookPro18,1 (#59 Item 17)
            (r'\bmac\d+,\d+', 'workstation', 'unknown', 0.9),  # Apple Silicon generic (Mac14,2) — ambiguous (#59 Item 17)
            (r'catalyst\s+\d+', 'network', 'switch', 1.0),  # Cisco switches
            (r'nexus\s+\d+', 'network', 'switch', 1.0),  # Cisco Nexus
            (r'fortigate[\s-]\d+', 'network', 'firewall', 1.0),  # Fortinet firewalls
            (r'asa[\s-]\d+', 'network', 'firewall', 1.0),  # Cisco ASA
        ]

    def _init_combined_patterns(self):
        """Initialize combined attribute patterns"""
        # OS + Manufacturer patterns for higher confidence
        self.combined_patterns = [
            # When we see specific combinations, we can be more confident
            (r'vmware', r'dell|hp|lenovo|cisco', 'server', 'hypervisor', 1.0),
            (r'windows\s+server', r'dell|hp|lenovo|ibm', 'server', 'server', 1.0),
            # Subcategory 'unknown' — without a model match we can't distinguish laptop vs desktop (#59 Item 13)
            (r'windows\s+1[01]', r'dell|hp|lenovo|asus', 'workstation', 'unknown', 1.0),
            # Generic 'windows' + known PC manufacturer → workstation (#26 Issue 1, Fix B)
            (r'^windows$', r'dell|hp|hewlett|lenovo|asus|acer|razer|cyberpower', 'workstation', 'unknown', 0.9),
            (r'ios', r'apple', 'mobile', 'phone', 1.0),
            (r'ipados', r'apple', 'mobile', 'tablet', 1.0),
            (r'android', r'^poly\b|^polycom', 'miscellaneous', 'voip', 1.0),  # Poly VoIP phones run Android (#58 Item 1)
            (r'android', r'samsung|google|motorola|lg', 'mobile', 'phone', 1.0),
            (r'printer', r'hp|canon|epson|brother', 'miscellaneous', 'printer', 1.0),
        ]

    def _init_hostname_patterns(self):
        """Initialize hostname-based fallback patterns (#26 Issue 1, Fix F)."""
        # Each pattern is (regex_pattern, category, subcategory, confidence)
        # Used as last-resort fallback when OS/manufacturer/model are unhelpful.
        self.hostname_patterns = [
            # Workstation patterns (Windows auto-generated names)
            (r'^laptop-', 'workstation', 'laptop', 0.85),
            (r'^desktop-', 'workstation', 'desktop', 0.85),
            # Server patterns (enterprise naming conventions)
            (r'srv', 'server', 'server', 0.7),
            (r'server', 'server', 'server', 0.7),
            (r'^dc[-_]|[-_]dc[-_]|[-_]dc\d', 'server', 'domain_controller', 0.7),
            (r'sql|[-_]db[-_]|^db[-_]', 'server', 'database', 0.7),
            # Enterprise naming conventions (#288) — validated against production data
            # Embedded device-type codes in continuous hostnames (e.g., CDELNNNB0283, CUKDT5545)
            (r'[a-z]nb\d', 'workstation', 'laptop', 0.55),        # 99.2% accuracy (4714 laptop vs 37 desktop)
            (r'[a-z]dt\d', 'workstation', 'desktop', 0.55),       # 99.8% accuracy (1165 desktop vs 2 laptop)
            # Site-code naming: {site}-{type}-{number} (e.g., SIN-N-212696, CAL-D-19053)
            (r'^[a-z]{2,5}-n-\d', 'workstation', 'laptop', 0.55), # 95.3% accuracy (221 laptop vs 11 desktop)
            (r'^[a-z]{2,5}-d-\d', 'workstation', 'desktop', 0.50), # 70% accuracy, low confidence
            # Delimited device-type segments (e.g., ACE-LT-0023, LAP055, AUWS8830)
            (r'(?:^|[-_])lap(?:[-_\d]|$)', 'workstation', 'laptop', 0.55),
            (r'(?:^|[-_])lt(?:[-_\d]|$)', 'workstation', 'laptop', 0.55),
            (r'[a-z]ws\d|(?:^|[-_])ws(?:[-_\d]|$)', 'workstation', 'desktop', 0.50),
            # Virtual machine / VDI patterns (e.g., CCV2-VM-032, azdevinvm01)
            (r'(?:^|[-_])vm(?:[-_\d]|$)|vm\d', 'server', 'virtual_server', 0.55),
            (r'(?:^|[-_])vdi(?:[-_\d]|$)', 'workstation', 'virtual_desktop', 0.55),
        ]

    def _normalize_string(self, value: str) -> str:
        """Normalize string for comparison"""
        if not isinstance(value, str):
            return ''
        normalized = value.lower().strip()
        # Remove extra whitespace
        normalized = self._whitespace_re.sub(' ', normalized)
        return normalized

    def _normalize_manufacturer(self, manufacturer: str) -> str:
        """Normalize manufacturer name for pattern matching"""
        normalized = self._normalize_string(manufacturer)
        # Apply normalization patterns
        for pattern, replacement in self.manufacturer_normalizations:
            normalized = pattern.sub(replacement, normalized)
        return normalized.strip()

    def _check_os_patterns(self, os: str) -> Optional[Tuple[str, str, float]]:
        """Check OS against patterns"""
        os_lower = self._normalize_string(os)

        for pattern, category, subcategory, confidence in self.os_patterns:
            if pattern.search(os_lower):
                return (category, subcategory, confidence)

        # Generic printer OS detection
        if 'printer' in os_lower and 'firmware' in os_lower:
            return ('miscellaneous', 'printer', 0.9)

        # Generic embedded OS detection
        if self._os_inline['embedded_fallback'].search(os_lower):
            return ('iot', 'embedded_pc', 0.7)

        return None

    def _check_manufacturer_patterns(self, manufacturer: str) -> Optional[Tuple[str, str, float]]:
        """Check manufacturer against patterns"""
        mfr_normalized = self._normalize_manufacturer(manufacturer)

        for pattern, category, subcategory, confidence in self.manufacturer_patterns:
            if pattern.search(mfr_normalized):
                return (category, subcategory, confidence)

        return None

    def _check_model_patterns(self, model: str) -> Optional[Tuple[str, str, float]]:
        """Check model against patterns"""
        model_lower = self._normalize_string(model)

        # Check patterns in priority order
        for pattern, category, subcategory, confidence in self.model_patterns:
            if pattern.search(model_lower):
                return (category, subcategory, confidence)

        return None

    def _check_combined_patterns(self, os: str, manufacturer: str, model: str) -> Optional[Tuple[str, str, float]]:
        """Check combined patterns for higher confidence"""
        os_lower = self._normalize_string(os) if os else ''
        mfr_normalized = self._normalize_manufacturer(manufacturer) if manufacturer else ''
        model_lower = self._normalize_string(model) if model else ''

        # Virtual machine detection — model indicates VM regardless of manufacturer (#58 Item 4)
        if model_lower and self._combined_inline['vm_model'].search(model_lower):
            if os_lower and self._combined_inline['server_os'].search(os_lower):
                return ('server', 'virtual_server', 1.0)
            elif os_lower and self._combined_inline['windows_client_os'].search(os_lower):
                return ('workstation', 'virtual_desktop', 1.0)
            elif os_lower and self._combined_inline['hypervisor_os'].search(os_lower):
                return ('server', 'hypervisor', 1.0)
            else:
                # No OS context — default to virtual_desktop (most common for Windows VMs)
                return ('workstation', 'virtual_desktop', 0.8)

        # Check manufacturer + model combinations FIRST — model is more specific
        # than OS for distinguishing laptop vs desktop (#58 Item 2)
        if mfr_normalized and model_lower:
            # Cisco + network device keywords
            if self._combined_inline['cisco_mfr'].search(mfr_normalized):
                if self._combined_inline['cisco_firewall_model'].search(model_lower):
                    return ('network', 'firewall', 1.0)
                elif self._combined_inline['cisco_router_model'].search(model_lower):
                    return ('network', 'router', 1.0)
                elif self._combined_inline['cisco_switch_model'].search(model_lower):
                    return ('network', 'switch', 1.0)
                elif self._combined_inline['cisco_hypervisor_model'].search(model_lower):
                    return ('server', 'hypervisor', 1.0)

            # Dell + device types
            if self._combined_inline['dell_mfr'].search(mfr_normalized):
                if self._combined_inline['dell_server_model'].search(model_lower):
                    # Refine subcategory using OS when available
                    if os_lower and self._combined_inline['hypervisor_os_full'].search(os_lower):
                        return ('server', 'hypervisor', 1.0)
                    return ('server', 'physical_server', 1.0)  # (#58 Item 6)
                elif self._combined_inline['dell_laptop_model'].search(model_lower):
                    return ('workstation', 'laptop', 1.0)
                elif self._combined_inline['dell_desktop_model'].search(model_lower):
                    return ('workstation', 'desktop', 1.0)
                # Precision: tower/sff/micro are desktops, mobile are laptops (#58 Item 2)
                elif self._combined_inline['dell_precision_model'].search(model_lower):
                    if self._combined_inline['precision_tower_model'].search(model_lower) and \
                       not self._combined_inline['precision_mobile_model'].search(model_lower):
                        return ('workstation', 'desktop', 1.0)
                    else:
                        return ('workstation', 'laptop', 1.0)

            # Lenovo + device types (#58 Item 2)
            if self._combined_inline['lenovo_mfr'].search(mfr_normalized):
                if self._combined_inline['lenovo_server_model'].search(model_lower):
                    if os_lower and self._combined_inline['hypervisor_os_full'].search(os_lower):
                        return ('server', 'hypervisor', 1.0)
                    return ('server', 'physical_server', 1.0)  # (#58 Item 6)
                elif self._combined_inline['lenovo_thinkpad_model'].search(model_lower):
                    return ('workstation', 'laptop', 1.0)
                elif self._combined_inline['lenovo_desktop_model'].search(model_lower):
                    return ('workstation', 'desktop', 1.0)
                # Lenovo machine-type prefix codes (#280)
                elif self._combined_inline['lenovo_laptop_machinetype'].search(model_lower):
                    return ('workstation', 'laptop', 1.0)
                elif self._combined_inline['lenovo_desktop_machinetype'].search(model_lower):
                    return ('workstation', 'desktop', 1.0)

            # HP + device types
            if self._combined_inline['hp_mfr'].search(mfr_normalized):
                if self._combined_inline['hp_server_model'].search(model_lower):
                    if os_lower and self._combined_inline['hypervisor_os_full'].search(os_lower):
                        return ('server', 'hypervisor', 1.0)
                    return ('server', 'physical_server', 1.0)  # (#58 Item 6)
                elif self._combined_inline['hp_laptop_model'].search(model_lower):
                    return ('workstation', 'laptop', 1.0)
                # HP desktop product lines (#58 Item 2)
                elif self._combined_inline['hp_pro_desktop_model'].search(model_lower):
                    return ('workstation', 'desktop', 1.0)
                elif self._combined_inline['hp_z_desktop_model'].search(model_lower):
                    return ('workstation', 'desktop', 1.0)
                elif self._combined_inline['hp_printer_model'].search(model_lower):
                    return ('miscellaneous', 'printer', 1.0)

            # Apple + device types (#58 Item 2)
            if self._combined_inline['apple_mfr'].search(mfr_normalized):
                if self._combined_inline['apple_macbook_model'].search(model_lower):
                    return ('workstation', 'laptop', 1.0)
                elif self._combined_inline['apple_desktop_model'].search(model_lower):
                    return ('workstation', 'desktop', 1.0)
                elif self._combined_inline['apple_ipad_model'].search(model_lower):
                    return ('mobile', 'tablet', 1.0)
                elif self._combined_inline['apple_iphone_model'].search(model_lower):
                    return ('mobile', 'phone', 1.0)
                elif self._combined_inline['apple_generic_mac_id'].search(model_lower):
                    # Mac14,2 etc — Apple Silicon, ambiguous laptop/desktop (#59 Item 17)
                    return ('workstation', 'unknown', 0.9)

            # Samsung + device types — distinguish tablets from phones (#58 Item 5)
            if self._combined_inline['samsung_mfr'].search(mfr_normalized):
                # SM-T* and SM-X* are tablets; SM-S/SM-G/SM-A/SM-F are phones
                if self._combined_inline['samsung_tablet_model'].search(model_lower):
                    return ('mobile', 'tablet', 1.0)
                elif self._combined_inline['samsung_phone_model'].search(model_lower):
                    return ('mobile', 'phone', 1.0)

        # OS + Manufacturer tuple patterns (less specific than manufacturer+model)
        for os_pattern, mfr_pattern, category, subcategory, confidence in self.combined_patterns:
            if os_lower and mfr_normalized:
                if os_pattern.search(os_lower) and mfr_pattern.search(mfr_normalized):
                    return (category, subcategory, confidence)

        return None

    def _check_mac_vendor_patterns(self, mac_vendor: str) -> Optional[Tuple[str, str, float]]:
        """Check MAC vendor as fallback with patterns"""
        # MAC vendor is less reliable, so we just normalize and check manufacturer patterns
        # but with lower confidence
        if mac_vendor:
            result = self._check_manufacturer_patterns(mac_vendor)
            if result:
                # Reduce confidence for MAC vendor-based predictions
                return (result[0], result[1], result[2] * 0.8)
        return None

    # Subcategories eligible for vendor_category refinement (#282)
    GENERIC_SUBCATEGORIES = {'unknown', 'server', 'phone'}

    # Vendor category mappings — explicit device type from data source (#59 Item 2)
    VENDOR_CATEGORY_MAP = {
        'laptop': ('workstation', 'laptop', 0.70),
        'desktop': ('workstation', 'desktop', 0.70),
        'server': ('server', 'server', 0.70),
        'kubernetes node': ('server', 'container_host', 0.65),
        'monitor': ('miscellaneous', 'monitor', 0.70),
        'printer': ('miscellaneous', 'printer', 0.70),
        'esxi server': ('server', 'hypervisor', 0.70),
        'vmware guest': ('server', 'virtual_server', 0.65),   # standardized from virtual_machine (#161 QA-15)
        'hyper-v guest': ('server', 'virtual_server', 0.70),  # standardized from virtual_machine (#161 QA-15)
        'nas': ('server', 'server', 0.65),
        'ftp server': ('server', 'server', 0.60),
        'apple mac': ('workstation', 'unknown', 0.60),
        'audio visual': ('miscellaneous', 'monitor', 0.70),
        'network': ('network', 'unknown', 0.65),
        'mobile': ('mobile', 'phone', 0.65),
        'smart office': ('iot', 'smart_device', 0.60),
        'smart watch': ('mobile', 'wearable', 0.70),
        'iot': ('iot', 'unknown', 0.60),
        'storage': ('server', 'storage', 0.65),
        'ip phone': ('miscellaneous', 'voip', 0.70),
        'security': ('iot', 'security_camera', 0.60),
        'gaming console': ('miscellaneous', 'unknown', 0.55),
        'remote access controller': ('server', 'management', 0.65),
        'firewall': ('network', 'firewall', 0.70),
        'embedded': ('iot', 'embedded_pc', 0.60),
        # Armis vocabulary
        'personal computers': ('workstation', 'unknown', 0.65),
        'plcs': ('ot', 'plc', 0.80),
        'plc': ('ot', 'plc', 0.80),
        'access points': ('network', 'access_point', 0.75),
        'ap': ('network', 'access_point', 0.75),
        'voips': ('miscellaneous', 'voip', 0.75),
        'voip': ('miscellaneous', 'voip', 0.75),
        'printers': ('miscellaneous', 'printer', 0.75),
        'switches': ('network', 'switch', 0.75),
        'ip cameras': ('iot', 'ip_camera', 0.75),
        'hmi panels': ('ot', 'hmi', 0.75),
        'hmi': ('ot', 'hmi', 0.75),
        "remote io's": ('ot', 'remote_io', 0.70),
        'industrial managed switches': ('ot', 'industrial_switch', 0.75),
        'engineering workstations': ('ot', 'engineering_workstation', 0.75),
        'media players': ('iot', 'media_player', 0.65),
        'scada servers': ('ot', 'scada_server', 0.80),
        'product scanners': ('ot', 'product_scanner', 0.70),
        'gateways': ('iot', 'gateway', 0.65),
        'tvs': ('iot', 'smart_tv', 0.65),
        'single-board computers': ('iot', 'embedded_pc', 0.65),
        'measuring instruments': ('ot', 'measuring_instrument', 0.70),
        'ac drives': ('ot', 'ac_drives', 0.80),
        'servo drives': ('ot', 'servo_drives', 0.80),
        'historians': ('ot', 'historian', 0.75),
        'controllers': ('ot', 'controller', 0.75),
        'appliances': ('miscellaneous', 'unknown', 0.60),
        'vcs': ('miscellaneous', 'video_conference', 0.75),
        'iot gateways': ('iot', 'gateway', 0.70),
        'lab equipment': ('ot', 'measuring_instrument', 0.65),
        'sensors': ('iot', 'sensor', 0.70),
        'storage server': ('server', 'storage', 0.70),
        # Juniper Mist vocabulary
        'switch': ('network', 'switch', 0.75),
        # ServiceNow sys_class_name values
        'cmdb_ci_computer': ('workstation', 'unknown', 0.60),
        'cmdb_ci_ip_switch': ('network', 'switch', 0.80),
        'cmdb_ci_printer': ('miscellaneous', 'printer', 0.80),
        'cmdb_ci_ip_router': ('network', 'router', 0.80),
        'cmdb_ci_ip_firewall': ('network', 'firewall', 0.80),
        # Common variants
        'servers': ('server', 'server', 0.70),
        'ups': ('miscellaneous', 'ups', 0.75),
        'tablets': ('mobile', 'tablet', 0.70),
        'thin client': ('workstation', 'thin_client', 0.70),
        'thin clients': ('workstation', 'thin_client', 0.70),
        'firewalls': ('network', 'firewall', 0.70),
        'routers': ('network', 'router', 0.70),
        'hotspots': ('network', 'access_point', 0.65),
        'projectors': ('miscellaneous', 'projector', 0.60),
        'speakers': ('iot', 'speaker', 0.60),
        'hypervisor': ('server', 'hypervisor', 0.75),
        'virtual machines': ('server', 'virtual_server', 0.70),
    }

    def _refine_with_vendor_category(self, result: Dict[str, Any], vendor_category: Optional[str]) -> Dict[str, Any]:
        """Refine an unknown subcategory using vendor_category if category matches."""
        if not vendor_category or (result.get('subcategory') is not None and result['subcategory'] not in self.GENERIC_SUBCATEGORIES):
            return result
        vc_lower = vendor_category.strip().lower()
        mapping = self.VENDOR_CATEGORY_MAP.get(vc_lower)
        if mapping and mapping[0] == result['category'] and mapping[1] != 'unknown':
            return {
                **result,
                'subcategory': mapping[1],
                'confidence': min(result['confidence'], mapping[2]),
                'rule_type': f"{result['rule_type']}+vendor_category",
            }
        return result

    @functools.lru_cache(maxsize=4096)
    def predict(self,
                os: Optional[str] = None,
                manufacturer: Optional[str] = None,
                model: Optional[str] = None,
                mac_vendor: Optional[str] = None,
                hostname: Optional[str] = None,
                vendor_category: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        Predict category and subcategory based on asset attributes using patterns.

        Args:
            os: Operating system
            manufacturer: Device manufacturer
            model: Device model
            mac_vendor: MAC address vendor
            hostname: Device hostname (used as last-resort fallback)

        Returns:
            Dict with 'category', 'subcategory', 'confidence', and 'rule_type'
            or None if no pattern matches
        """

        # Priority 1: Combined patterns (highest confidence)
        if (os and manufacturer) or ((os or manufacturer) and model):
            result = self._check_combined_patterns(os, manufacturer, model)
            if result and result[2] >= 0.9:  # High confidence combined patterns
                return self._refine_with_vendor_category({
                    'category': result[0],
                    'subcategory': result[1],
                    'confidence': result[2],
                    'rule_type': 'combined_patterns'
                }, vendor_category)

        # Priority 2: OS patterns
        if os:
            result = self._check_os_patterns(os)
            if result and result[2] >= 0.95:  # High confidence OS patterns
                return self._refine_with_vendor_category({
                    'category': result[0],
                    'subcategory': result[1],
                    'confidence': result[2],
                    'rule_type': 'os_pattern'
                }, vendor_category)

        # Priority 3: Model patterns
        if model:
            result = self._check_model_patterns(model)
            if result and result[2] >= 0.9:
                return self._refine_with_vendor_category({
                    'category': result[0],
                    'subcategory': result[1],
                    'confidence': result[2],
                    'rule_type': 'model_pattern'
                }, vendor_category)

        # Priority 4: Lower confidence OS patterns
        if os:
            result = self._check_os_patterns(os)
            if result:
                return self._refine_with_vendor_category({
                    'category': result[0],
                    'subcategory': result[1],
                    'confidence': result[2],
                    'rule_type': 'os_pattern'
                }, vendor_category)

        # Priority 5: Manufacturer patterns
        if manufacturer:
            result = self._check_manufacturer_patterns(manufacturer)
            if result:
                return self._refine_with_vendor_category({
                    'category': result[0],
                    'subcategory': result[1],
                    'confidence': result[2],
                    'rule_type': 'manufacturer_pattern'
                }, vendor_category)

        # Priority 6: Vendor category — explicit device type from source (#59 Item 2)
        if vendor_category:
            vc_lower = vendor_category.strip().lower()
            mapping = self.VENDOR_CATEGORY_MAP.get(vc_lower)
            if mapping:
                return {
                    'category': mapping[0],
                    'subcategory': mapping[1],
                    'confidence': mapping[2],
                    'rule_type': 'vendor_category'
                }

        # Priority 7: Lower confidence model patterns
        if model:
            result = self._check_model_patterns(model)
            if result:
                return self._refine_with_vendor_category({
                    'category': result[0],
                    'subcategory': result[1],
                    'confidence': result[2],
                    'rule_type': 'model_pattern'
                }, vendor_category)

        # Priority 8: MAC vendor patterns
        if mac_vendor:
            result = self._check_mac_vendor_patterns(mac_vendor)
            if result:
                return self._refine_with_vendor_category({
                    'category': result[0],
                    'subcategory': result[1],
                    'confidence': result[2],
                    'rule_type': 'mac_vendor_pattern'
                }, vendor_category)

        # Priority 9: Hostname pattern fallback (#26 Issue 1, Fix F)
        if hostname:
            hostname_lower = self._normalize_string(hostname)
            if hostname_lower:
                for pattern, category, subcategory, confidence in self.hostname_patterns:
                    if pattern.search(hostname_lower):
                        return self._refine_with_vendor_category({
                            'category': category,
                            'subcategory': subcategory,
                            'confidence': confidence,
                            'rule_type': 'hostname_pattern'
                        }, vendor_category)

        # No pattern matches
        return None

    def get_stats(self) -> Dict[str, int]:
        """Get statistics about the loaded patterns"""
        return {
            'os_patterns': len(self.os_patterns),
            'manufacturer_patterns': len(self.manufacturer_patterns),
            'model_patterns': len(self.model_patterns),
            'combined_patterns': len(self.combined_patterns),
            'manufacturer_normalizations': len(self.manufacturer_normalizations),
            'hostname_patterns': len(self.hostname_patterns),
        }


# Example usage and testing
if __name__ == "__main__":
    predictor = PatternBasedCategoryPredictor()

    # Print pattern statistics
    stats = predictor.get_stats()
    print("Pattern Statistics:")
    for pattern_type, count in stats.items():
        print(f"  {pattern_type}: {count}")

    print("\n" + "="*70 + "\n")

    # Test cases showing pattern flexibility
    test_cases = [
        {
            'name': 'Windows 11 (current)',
            'input': {'os': 'Windows 11', 'manufacturer': 'Dell', 'model': 'Latitude 5520'},
            'description': 'Current Windows version'
        },
        {
            'name': 'Windows 12 (future)',
            'input': {'os': 'Windows 12 Pro', 'manufacturer': 'HP', 'model': 'EliteBook 850'},
            'description': 'Future Windows version - pattern should still work'
        },
        {
            'name': 'VMware ESXi 9.0 (future)',
            'input': {'os': 'VMware ESXi 9.0', 'manufacturer': 'Dell Technologies', 'model': 'PowerEdge R750'},
            'description': 'Future ESXi version with normalized manufacturer'
        },
        {
            'name': 'Ubuntu Server 24.04 LTS (future)',
            'input': {'os': 'Ubuntu Server 24.04 LTS', 'manufacturer': 'Lenovo Global Technology Limited'},
            'description': 'Future Ubuntu Server with full manufacturer name'
        },
        {
            'name': 'iOS 20 (future)',
            'input': {'os': 'iOS 20.1', 'manufacturer': 'Apple Inc.', 'model': 'iPhone 20 Pro Max'},
            'description': 'Future iOS version'
        },
        {
            'name': 'Cisco IOS-XE 20.x (future)',
            'input': {'os': 'Cisco IOS-XE 20.3.1', 'manufacturer': 'Cisco Systems, Inc.', 'model': 'Catalyst 9300-48P'},
            'description': 'Future network OS with version'
        },
        {
            'name': 'Normalized printer manufacturer',
            'input': {'manufacturer': 'Canon Corporation', 'model': 'imageRUNNER ADVANCE C5535i III'},
            'description': 'Manufacturer with suffix that gets normalized'
        },
        {
            'name': 'Pattern-based printer OS',
            'input': {'os': 'Custom Printer Firmware v3.2.1'},
            'description': 'Generic printer firmware detection'
        },
        {
            'name': 'Model with multiple keywords',
            'input': {'model': 'ProLiant DL380 Gen11 Rack Server'},
            'description': 'Multiple patterns in model'
        },
        {
            'name': 'Partial manufacturer match',
            'input': {'manufacturer': 'Hewlett-Packard Enterprise Company', 'model': 'LaserJet Enterprise M608'},
            'description': 'HP with various suffixes'
        },
        {
            'name': 'Future Rockwell PLC',
            'input': {'os': 'Studio 5000 v35', 'manufacturer': 'Rockwell Automation, Inc.'},
            'description': 'Future version of Studio 5000'
        },
        {
            'name': 'Generic embedded device',
            'input': {'os': 'Custom RTOS v2.0', 'model': 'Industrial Controller'},
            'description': 'Generic embedded OS detection'
        },
    ]

    print("Pattern-Based Predictions (showing flexibility):\n")
    for test in test_cases:
        print(f"Test: {test['name']}")
        print(f"  Description: {test['description']}")
        print(f"  Input: OS='{test['input'].get('os')}', Manufacturer='{test['input'].get('manufacturer')}', Model='{test['input'].get('model')}'")

        result = predictor.predict(**test['input'])
        if result:
            print(f"  Result: {result['category']}/{result['subcategory']}")
            print(f"  Confidence: {result['confidence']:.2%}, Method: {result['rule_type']}")
        else:
            print(f"  Result: No pattern match (would fall back to ML/LLM)")
        print()

    print("="*70)
    print("\nKey Improvements:")
    print("1. Version-agnostic OS patterns (e.g., Windows *, VMware ESXi *, iOS *)")
    print("2. Manufacturer normalization (removes Inc., Corp., Ltd., etc.)")
    print("3. Regex patterns for flexible matching")
    print("4. Model number patterns (e.g., PowerEdge R*, Catalyst *, etc.)")
    print("5. Combined patterns for higher confidence")
    print("6. Graceful degradation with confidence scores")