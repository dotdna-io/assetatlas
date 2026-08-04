# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Vendor-Specific Serial Number Validators

This module provides vendor-specific validation and metadata extraction
for serial numbers from major hardware manufacturers.

Supported Vendors:
- Dell (Service Tags)
- HP (Serial Numbers)
- Apple (Serial Numbers with date extraction)
- Cisco (Serial Numbers)
- Lenovo (Serial Numbers)
- Samsung (Mobile Device Serial Numbers)
- Microsoft Surface (Serial Numbers)
- IBM (Serial Numbers with type code)
"""

import logging
import re
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, Tuple

logger = logging.getLogger(__name__)


class VendorSNValidator(ABC):
    """Base class for vendor-specific serial number validation."""

    @abstractmethod
    def validate_format(self, sn: str) -> bool:
        """
        Validate that serial number matches vendor-specific format.

        Args:
            sn: Serial number to validate

        Returns:
            True if format is valid, False otherwise
        """
        pass

    @abstractmethod
    def extract_metadata(self, sn: str) -> Dict[str, Any]:
        """
        Extract vendor-specific metadata from serial number.

        Common metadata includes:
        - manufacture_year
        - manufacture_week
        - manufacture_location
        - model_hint

        Args:
            sn: Serial number to analyze

        Returns:
            Dictionary of extracted metadata (empty if no metadata available)
        """
        pass

    @property
    @abstractmethod
    def vendor_name(self) -> str:
        """Return vendor name."""
        pass


class DellSNValidator(VendorSNValidator):
    """
    Dell Service Tag validator.

    Format: 7-character alphanumeric (case-insensitive)
    Example: J8HGF32, 5XYZ123

    Note: Dell also has Express Service Code (10-11 digit numeric),
    but Service Tag is the primary identifier.
    """

    @property
    def vendor_name(self) -> str:
        return "Dell"

    def validate_format(self, sn: str) -> bool:
        """
        Validate Dell Service Tag format.

        Rules:
        - Exactly 7 characters
        - Alphanumeric only (A-Z, 0-9)
        - Case insensitive

        Args:
            sn: Serial number to validate

        Returns:
            True if valid Dell Service Tag format
        """
        if not sn or not isinstance(sn, str):
            return False

        sn = sn.strip().upper()

        # Must be exactly 7 alphanumeric characters
        if not re.match(r'^[A-Z0-9]{7}$', sn):
            return False

        # Additional heuristic: very unlikely to be all same character
        # (already handled by main validator, but good defense-in-depth)
        if len(set(sn)) == 1:
            return False

        return True

    def extract_metadata(self, sn: str) -> Dict[str, Any]:
        """
        Extract metadata from Dell Service Tag.

        Note: Dell Service Tags don't publicly expose manufacturing
        date or location information in the serial number itself.
        This information is available via Dell's API but not encoded
        in the tag format.

        Args:
            sn: Dell Service Tag

        Returns:
            Dictionary with format info only
        """
        if not self.validate_format(sn):
            return {}

        return {
            'vendor': 'Dell',
            'format': 'Service Tag (7-char)',
            'validated': True
        }


class HPSNValidator(VendorSNValidator):
    """
    HP Serial Number validator.

    Format: 10-character alphanumeric
    Example: CND1234ABC, 5CD9012XYZ

    Common patterns:
    - Often starts with country code (CN=China, US=USA, etc.)
    - May have letters indicating production facility
    """

    @property
    def vendor_name(self) -> str:
        return "HP"

    def validate_format(self, sn: str) -> bool:
        """
        Validate HP Serial Number format.

        Rules:
        - Typically 10 characters
        - Alphanumeric only
        - Case insensitive

        Args:
            sn: Serial number to validate

        Returns:
            True if valid HP format
        """
        if not sn or not isinstance(sn, str):
            return False

        sn = sn.strip().upper()

        # HP SNs are typically 10 characters, sometimes 8-12
        if not re.match(r'^[A-Z0-9]{8,12}$', sn):
            return False

        # Most common length is 10
        if len(sn) == 10:
            return True

        # Accept 8-12 range but log for monitoring
        logger.debug(f"HP SN with non-standard length {len(sn)}: {sn}")
        return True

    def extract_metadata(self, sn: str) -> Dict[str, Any]:
        """
        Extract metadata from HP Serial Number.

        HP SNs may contain country/facility codes in first 2-3 characters.

        Args:
            sn: HP Serial Number

        Returns:
            Dictionary with format and potential location hints
        """
        if not self.validate_format(sn):
            return {}

        sn = sn.strip().upper()

        metadata = {
            'vendor': 'HP',
            'format': f'HP SN ({len(sn)}-char)',
            'validated': True
        }

        # Country code detection (first 2-3 chars)
        country_codes = {
            'CN': 'China',
            'US': 'USA',
            'SG': 'Singapore',
            'MY': 'Malaysia',
            'TW': 'Taiwan',
        }

        if len(sn) >= 2:
            potential_code = sn[:2]
            if potential_code in country_codes:
                metadata['manufacture_country_hint'] = country_codes[potential_code]

        return metadata


class AppleSNValidator(VendorSNValidator):
    """
    Apple Serial Number validator with manufacturing date extraction.

    Three formats:

    OLD (11-digit, pre-2012):
    - Position 1-2: Factory code
    - Position 3: Year (0-9 for 2010/2001-2009)
    - Position 4-5: Week (01-52)
    - Position 6-8: Unique identifier
    - Position 9-11: Model identifier

    MIDDLE (12-digit, 2010-2020):
    - Position 1-3: Factory code
    - Position 4: Year (base-27: C=2010-H1, D=2010-H2, F=2011-H1...)
    - Position 5: Week (base-27: 1-9, C-X for weeks 1-26)
    - Position 6-8: Unique identifier
    - Position 9-12: Model identifier

    NEW (10-digit, 2021+):
    - Position 1-2: Product line identifier
    - Position 3-6: Manufacturing location and date code
    - Position 7-10: Unique device identifier
    - NOTE: Date extraction not possible (randomized format)
    """

    # Year mapping for 12-digit format (base-27 encoding)
    YEAR_MAP_12 = {
        'C': (2010, 1), 'D': (2010, 2), 'F': (2011, 1), 'G': (2011, 2),
        'H': (2012, 1), 'J': (2012, 2), 'K': (2013, 1), 'L': (2013, 2),
        'M': (2014, 1), 'N': (2014, 2), 'P': (2015, 1), 'Q': (2015, 2),
        'R': (2016, 1), 'S': (2016, 2), 'T': (2017, 1), 'V': (2017, 2),
        'W': (2018, 1), 'X': (2018, 2), 'Y': (2019, 1), 'Z': (2019, 2),
    }
    # Note: Apple transitioned to randomized serial numbers in late 2020.
    # The 12-digit format with year encoding (shown above) only applies to
    # devices manufactured before this transition. No additional year codes
    # need to be added since Apple no longer uses this encoding scheme.

    # Week mapping for 12-digit format (base-27 encoding)
    WEEK_MAP_12 = {
        '1': 1, '2': 2, '3': 3, '4': 4, '5': 5, '6': 6, '7': 7, '8': 8, '9': 9,
        'C': 10, 'D': 11, 'F': 12, 'G': 13, 'H': 14, 'J': 15, 'K': 16,
        'L': 17, 'M': 18, 'N': 19, 'P': 20, 'Q': 21, 'R': 22, 'T': 23,
        'V': 24, 'W': 25, 'X': 26,
    }

    # Year mapping for 11-digit format
    YEAR_MAP_11 = {
        '0': 2010, '1': 2001, '2': 2002, '3': 2003, '4': 2004,
        '5': 2005, '6': 2006, '7': 2007, '8': 2008, '9': 2009,
    }

    @property
    def vendor_name(self) -> str:
        return "Apple"

    def validate_format(self, sn: str) -> bool:
        """
        Validate Apple Serial Number format.

        Rules:
        - 10, 11 or 12 characters (RESEARCH FINDING: 10-char format added 2021+)
        - Alphanumeric only
        - Case insensitive

        Args:
            sn: Serial number to validate

        Returns:
            True if valid Apple SN format
        """
        if not sn or not isinstance(sn, str):
            return False

        sn = sn.strip().upper()

        # Must be 10, 11 or 12 alphanumeric characters
        if not re.match(r'^[A-Z0-9]{10,12}$', sn):
            return False

        # 10-character format (2021+): No date validation possible (randomized)
        if len(sn) == 10:
            # Just verify it's alphanumeric (already done above)
            return True

        # Additional validation for older formats: check if year/week codes are valid
        if len(sn) == 12:
            year_char = sn[3]
            week_char = sn[4]
            # Year must be in valid range
            if year_char not in self.YEAR_MAP_12:
                return False
            # Week must be in valid range
            if week_char not in self.WEEK_MAP_12:
                return False
        elif len(sn) == 11:
            year_char = sn[2]
            week_str = sn[3:5]
            # Year must be digit
            if year_char not in self.YEAR_MAP_11:
                return False
            # Week must be numeric and valid (01-52)
            if not week_str.isdigit():
                return False
            week_num = int(week_str)
            if not (1 <= week_num <= 52):
                return False

        return True

    def extract_metadata(self, sn: str) -> Dict[str, Any]:
        """
        Extract manufacturing date from Apple Serial Number.

        This is one of the most valuable vendor-specific extractions
        as it provides concrete manufacturing date information.
        NOTE: 10-character format (2021+) uses randomized encoding, date extraction not possible.

        Args:
            sn: Apple Serial Number

        Returns:
            Dictionary with manufacturing year, week, and format info
        """
        if not self.validate_format(sn):
            return {}

        sn = sn.strip().upper()

        metadata = {
            'vendor': 'Apple',
            'validated': True
        }

        if len(sn) == 10:
            # RESEARCH FINDING: New randomized format (2021+)
            # Cannot extract date - format is randomized for security
            metadata.update({
                'format': 'Apple-10digit-randomized',
                'note': 'Manufacturing date not extractable (randomized format)'
            })

        elif len(sn) == 12:
            # Middle format (2010-2020)
            year_char = sn[3]
            week_char = sn[4]
            factory_code = sn[:3]

            year, half = self.YEAR_MAP_12.get(year_char, (None, None))
            week = self.WEEK_MAP_12.get(week_char, 0)

            if year and week:
                # Half=2 means second half of year (weeks 27-52)
                if half == 2:
                    week += 26

                metadata.update({
                    'manufacture_year': year,
                    'manufacture_week': week,
                    'factory_code': factory_code,
                    'format': 'Apple-12digit-middle'
                })

        elif len(sn) == 11:
            # Old format (pre-2012)
            year_char = sn[2]
            week_str = sn[3:5]
            factory_code = sn[:2]

            year = self.YEAR_MAP_11.get(year_char)
            week = int(week_str) if week_str.isdigit() else 0

            if year and 1 <= week <= 52:
                metadata.update({
                    'manufacture_year': year,
                    'manufacture_week': week,
                    'factory_code': factory_code,
                    'format': 'Apple-11digit-old'
                })

        return metadata


class CiscoSNValidator(VendorSNValidator):
    """
    Cisco Serial Number validator.

    Format: Variable length (typically 11-13 characters)
    Pattern: Often starts with 3-letter prefix indicating product family
    Example: FOC1234ABCD, FXS5678WXYZ

    Common prefixes:
    - FOC: Catalyst switches
    - FXS: ASR routers
    - JAE: Small business switches
    - SAL: Older equipment
    """

    COMMON_PREFIXES = {
        'FOC', 'FXS', 'JAE', 'SAL', 'FCW', 'FTX', 'FHK', 'CAT',
        'JMX', 'KWC', 'DTI', 'INM', 'SCA', 'FCZ', 'FGL'
    }

    @property
    def vendor_name(self) -> str:
        return "Cisco"

    def validate_format(self, sn: str) -> bool:
        """
        Validate Cisco Serial Number format.

        Rules:
        - Variable length, typically 11-13 characters
        - Alphanumeric only
        - Often starts with known 3-letter prefix

        Args:
            sn: Serial number to validate

        Returns:
            True if valid Cisco SN format
        """
        if not sn or not isinstance(sn, str):
            return False

        sn = sn.strip().upper()

        # Typical range is 10-13 characters, accept 8-15 to be safe
        if not re.match(r'^[A-Z0-9]{8,15}$', sn):
            return False

        # Optional: Check for known prefix (strong indicator)
        if len(sn) >= 3:
            prefix = sn[:3]
            if prefix in self.COMMON_PREFIXES:
                logger.debug(f"Cisco SN matched known prefix: {prefix}")
                return True

        # Accept if in general alphanumeric format
        # (not all Cisco SNs have known prefixes)
        return True

    def extract_metadata(self, sn: str) -> Dict[str, Any]:
        """
        Extract metadata from Cisco Serial Number.

        Cisco SNs may contain product family hints in the prefix.

        Args:
            sn: Cisco Serial Number

        Returns:
            Dictionary with format and potential product family hints
        """
        if not self.validate_format(sn):
            return {}

        sn = sn.strip().upper()

        metadata = {
            'vendor': 'Cisco',
            'format': f'Cisco SN ({len(sn)}-char)',
            'validated': True
        }

        # Product family detection from prefix
        if len(sn) >= 3:
            prefix = sn[:3]
            if prefix in self.COMMON_PREFIXES:
                metadata['prefix'] = prefix
                # Add known product family hints
                prefix_hints = {
                    'FOC': 'Catalyst Switch',
                    'FXS': 'ASR Router',
                    'JAE': 'Small Business Switch',
                    'SAL': 'Legacy Equipment',
                }
                if prefix in prefix_hints:
                    metadata['product_family_hint'] = prefix_hints[prefix]

        return metadata


class LenovoSNValidator(VendorSNValidator):
    """
    Lenovo Serial Number validator.

    Format: Variable length (typically 10 characters)
    Pattern: Model-dependent
    Example: PF1ABC23, S1234567890

    Note: Lenovo also uses MTM (Machine Type Model) as additional identifier
    """

    @property
    def vendor_name(self) -> str:
        return "Lenovo"

    def validate_format(self, sn: str) -> bool:
        """
        Validate Lenovo Serial Number format.

        Rules:
        - Variable length, typically 10 characters (range 8-12)
        - Alphanumeric only
        - Case insensitive

        Args:
            sn: Serial number to validate

        Returns:
            True if valid Lenovo SN format
        """
        if not sn or not isinstance(sn, str):
            return False

        sn = sn.strip().upper()

        # Lenovo SNs are typically 10 characters, accept 8-12 range
        if not re.match(r'^[A-Z0-9]{8,12}$', sn):
            return False

        return True

    def extract_metadata(self, sn: str) -> Dict[str, Any]:
        """
        Extract metadata from Lenovo Serial Number.

        Limited metadata available from SN alone.

        Args:
            sn: Lenovo Serial Number

        Returns:
            Dictionary with format info
        """
        if not self.validate_format(sn):
            return {}

        return {
            'vendor': 'Lenovo',
            'format': f'Lenovo SN ({len(sn)}-char)',
            'validated': True
        }


class SamsungSNValidator(VendorSNValidator):
    """
    Samsung mobile device serial number validator.

    Format: 10+ alphanumeric characters starting with R, S, or F prefix.
    Examples: RF8N90VT6XA, S5NKAB12345
    """

    SAMSUNG_PREFIXES = {'R', 'S', 'F'}

    @property
    def vendor_name(self) -> str:
        return "Samsung"

    def validate_format(self, sn: str) -> bool:
        """
        Validate Samsung serial number format.

        Rules:
        - At least 10 characters
        - Alphanumeric only
        - Must start with R, S, or F

        Args:
            sn: Serial number to validate

        Returns:
            True if valid Samsung SN format
        """
        if not sn or not isinstance(sn, str):
            return False

        sn = sn.strip().upper()

        if len(sn) < 10:
            return False

        return sn[0] in self.SAMSUNG_PREFIXES and sn.isalnum()

    def extract_metadata(self, sn: str) -> Dict[str, Any]:
        """
        Extract metadata from Samsung serial number.

        Args:
            sn: Samsung Serial Number

        Returns:
            Dictionary with vendor and format info
        """
        if not self.validate_format(sn):
            return {}

        return {'vendor': 'Samsung', 'format': 'samsung_mobile'}


class MicrosoftSurfaceSNValidator(VendorSNValidator):
    """
    Microsoft Surface serial number validator.

    Format: Exactly 12 digits.
    Examples: 012345678901
    """

    @property
    def vendor_name(self) -> str:
        return "Microsoft"

    def validate_format(self, sn: str) -> bool:
        """
        Validate Microsoft Surface serial number format.

        Rules:
        - Exactly 12 characters
        - All digits

        Args:
            sn: Serial number to validate

        Returns:
            True if valid Surface SN format
        """
        if not sn or not isinstance(sn, str):
            return False

        sn = sn.strip()

        if len(sn) != 12:
            return False

        return sn.isdigit()

    def extract_metadata(self, sn: str) -> Dict[str, Any]:
        """
        Extract metadata from Microsoft Surface serial number.

        Args:
            sn: Surface Serial Number

        Returns:
            Dictionary with vendor and format info
        """
        if not self.validate_format(sn):
            return {}

        return {'vendor': 'Microsoft', 'format': 'surface'}


class IBMSNValidator(VendorSNValidator):
    """
    IBM serial number validator.

    Format: 7 characters -- 2-digit type code + 5 alphanumeric characters.
    Examples: 23ABC45
    """

    @property
    def vendor_name(self) -> str:
        return "IBM"

    def validate_format(self, sn: str) -> bool:
        """
        Validate IBM serial number format.

        Rules:
        - Exactly 7 characters
        - First 2 characters must be digits (type code)
        - Remaining 5 characters must be alphanumeric

        Args:
            sn: Serial number to validate

        Returns:
            True if valid IBM SN format
        """
        if not sn or not isinstance(sn, str):
            return False

        sn = sn.strip().upper()

        if len(sn) != 7:
            return False

        return sn[:2].isdigit() and sn[2:].isalnum()

    def extract_metadata(self, sn: str) -> Dict[str, Any]:
        """
        Extract metadata from IBM serial number.

        The first 2 digits are the IBM type code identifying the
        machine type.

        Args:
            sn: IBM Serial Number

        Returns:
            Dictionary with vendor, format, and type code
        """
        if not self.validate_format(sn):
            return {}

        sn = sn.strip().upper()

        return {
            'vendor': 'IBM',
            'format': 'ibm_type_serial',
            'type_code': sn[:2],
        }


class VendorDetector:
    """
    Detect vendor from serial number format and provide appropriate validator.
    """

    def __init__(self):
        """Initialize vendor detector with all validators."""
        self.validators = {
            'dell': DellSNValidator(),
            'hp': HPSNValidator(),
            'apple': AppleSNValidator(),
            'cisco': CiscoSNValidator(),
            'lenovo': LenovoSNValidator(),
            'samsung': SamsungSNValidator(),
            'microsoft_surface': MicrosoftSurfaceSNValidator(),
            'ibm': IBMSNValidator(),
        }

    def detect_vendor(self, sn: str) -> Optional[Tuple[str, VendorSNValidator]]:
        """
        Attempt to detect vendor from serial number format.

        Args:
            sn: Serial number to analyze

        Returns:
            Tuple of (vendor_name, validator) if detected, None otherwise
        """
        if not sn or not isinstance(sn, str):
            return None

        sn = sn.strip().upper()

        # Try each validator in order of specificity
        # (most specific patterns first)

        # Cisco: Check prefix first — FOC-prefixed 7-char SNs would
        # otherwise match Dell's format (#16).
        # A known prefix is a strong enough signal even for shorter SNs
        # that fall below Cisco's standard 8-char minimum.
        cisco_validator = self.validators['cisco']
        if len(sn) >= 7 and sn[:3] in cisco_validator.COMMON_PREFIXES:
            if sn.isalnum():
                return ('Cisco', cisco_validator)

        # IBM: 7-char with 2-digit type prefix (more specific than Dell)
        if self.validators['ibm'].validate_format(sn):
            return ('IBM', self.validators['ibm'])

        # Dell: 7-char alphanumeric (general)
        if self.validators['dell'].validate_format(sn):
            return ('Dell', self.validators['dell'])

        # Samsung: 10+ chars starting with R/S/F prefix
        if self.validators['samsung'].validate_format(sn):
            return ('Samsung', self.validators['samsung'])

        # Apple: 10-12 chars with specific year/week encoding
        if self.validators['apple'].validate_format(sn):
            return ('Apple', self.validators['apple'])

        # Microsoft Surface: exactly 12 digits (after Apple, which rejects all-digit)
        if self.validators['microsoft_surface'].validate_format(sn):
            return ('Microsoft', self.validators['microsoft_surface'])

        # HP: 10 chars (most common)
        if len(sn) == 10 and self.validators['hp'].validate_format(sn):
            return ('HP', self.validators['hp'])

        # Lenovo: Less specific, try last
        if self.validators['lenovo'].validate_format(sn):
            return ('Lenovo', self.validators['lenovo'])

        # Cisco: Try without prefix requirement
        if self.validators['cisco'].validate_format(sn):
            return ('Cisco', self.validators['cisco'])

        return None

    def get_validator(self, vendor_name: str) -> Optional[VendorSNValidator]:
        """
        Get validator by vendor name.

        Args:
            vendor_name: Vendor name (case-insensitive)

        Returns:
            Validator instance or None if not found
        """
        return self.validators.get(vendor_name.lower())
