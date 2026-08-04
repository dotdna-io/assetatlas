# SPDX-License-Identifier: AGPL-3.0-or-later
"""
AssetAtlas Backend API Test Script

Tests basic functionality of the AssetAtlas Backend API endpoints,
specifically the Tag Rules endpoints.

Usage:
    python tag_rules/api/test_api.py

Make sure the API server is running before running this script:
    python tag_rules/api/app.py
    or: ./tag_rules/api/start.sh
"""

import requests
import json
import sys
from typing import Dict, Any

BASE_URL = "http://localhost:5001/api"


def print_test(name: str):
    """Print test name"""
    print(f"\n{'='*60}")
    print(f"TEST: {name}")
    print('='*60)


def print_result(success: bool, message: str):
    """Print test result"""
    status = "✅ PASS" if success else "❌ FAIL"
    print(f"{status}: {message}")


def test_health_check():
    """Test health check endpoint"""
    print_test("Health Check")

    try:
        response = requests.get(f"{BASE_URL}/health")
        data = response.json()

        if response.status_code == 200 and data['status'] == 'healthy':
            print_result(True, "API is healthy and database is connected")
            return True
        else:
            print_result(False, f"Health check failed: {data}")
            return False

    except Exception as e:
        print_result(False, f"Error: {e}")
        return False


def test_get_fields():
    """Test get available fields endpoint"""
    print_test("Get Available Fields")

    try:
        response = requests.get(f"{BASE_URL}/tag-rules/fields")
        data = response.json()

        if response.status_code == 200 and 'target_fields' in data:
            field_count = len(data['target_fields'])
            print_result(True, f"Retrieved {field_count} target fields")
            print(f"Sample field: {data['target_fields'][0]['name']}")
            return True
        else:
            print_result(False, f"Failed to get fields: {data}")
            return False

    except Exception as e:
        print_result(False, f"Error: {e}")
        return False


def test_preview_classification():
    """Test preview endpoint for classification rule"""
    print_test("Preview Classification Rule")

    try:
        payload = {
            "target_filters": [
                {
                    "field": "OS Type",
                    "operator": "contains",
                    "value": "Windows"
                }
            ]
        }

        response = requests.post(f"{BASE_URL}/tag-rules/preview", json=payload)
        data = response.json()

        if response.status_code == 200:
            count = data['total_matching']
            time_ms = data['execution_time_ms']
            print_result(True, f"Found {count} matching assets in {time_ms}ms")
            return True
        else:
            print_result(False, f"Preview failed: {data}")
            return False

    except Exception as e:
        print_result(False, f"Error: {e}")
        return False


def test_preview_compliance():
    """Test preview endpoint for compliance rule"""
    print_test("Preview Compliance Rule")

    try:
        payload = {
            "target_filters": [
                {
                    "field": "Asset Type",
                    "operator": "equals",
                    "value": "workstation"
                }
            ],
            "compliance_criteria": {
                "logic": "any",
                "conditions": [
                    {
                        "field": "Software Installed",
                        "operator": "contains",
                        "value": "Defender"
                    }
                ]
            }
        }

        response = requests.post(f"{BASE_URL}/tag-rules/preview", json=payload)
        data = response.json()

        if response.status_code == 200:
            total = data['total_matching']
            passed = data.get('pass_count', 0)
            failed = data.get('fail_count', 0)
            time_ms = data['execution_time_ms']
            print_result(
                True,
                f"Found {total} assets: {passed} pass, {failed} fail in {time_ms}ms"
            )
            return True
        else:
            print_result(False, f"Compliance preview failed: {data}")
            return False

    except Exception as e:
        print_result(False, f"Error: {e}")
        return False


def test_create_classification_rule():
    """Test creating a classification rule"""
    print_test("Create Classification Rule")

    try:
        payload = {
            "name": "Test Windows Classification",
            "description": "Test rule for Windows assets",
            "type": "classification",
            "enabled": True,
            "target_filters": [
                {
                    "field": "OS Type",
                    "operator": "contains",
                    "value": "Windows"
                }
            ],
            "tags": {
                "label": "Test-Windows",
                "color": "#0078d4"
            }
        }

        response = requests.post(f"{BASE_URL}/tag-rules", json=payload)
        data = response.json()

        if response.status_code == 201 and data['success']:
            rule_id = data['rule']['id']
            assets_matched = data['rule'].get('last_execution_assets_matched', 0)
            print_result(
                True,
                f"Created rule {rule_id}, matched {assets_matched} assets"
            )
            return rule_id
        else:
            print_result(False, f"Failed to create rule: {data}")
            return None

    except Exception as e:
        print_result(False, f"Error: {e}")
        return None


def test_list_rules():
    """Test listing rules"""
    print_test("List Rules")

    try:
        response = requests.get(f"{BASE_URL}/tag-rules")
        data = response.json()

        if response.status_code == 200 and data['success']:
            count = len(data['rules'])
            total = data['pagination']['total']
            print_result(True, f"Listed {count} rules (total: {total})")

            if count > 0:
                print(f"Sample rule: {data['rules'][0]['name']}")
            return True
        else:
            print_result(False, f"Failed to list rules: {data}")
            return False

    except Exception as e:
        print_result(False, f"Error: {e}")
        return False


def test_get_rule(rule_id: str):
    """Test getting a specific rule"""
    print_test(f"Get Rule: {rule_id}")

    try:
        response = requests.get(f"{BASE_URL}/tag-rules/{rule_id}")
        data = response.json()

        if response.status_code == 200 and data['success']:
            rule_name = data['rule']['name']
            exec_count = data['rule']['execution_count']
            print_result(True, f"Retrieved rule '{rule_name}', executed {exec_count} times")
            return True
        else:
            print_result(False, f"Failed to get rule: {data}")
            return False

    except Exception as e:
        print_result(False, f"Error: {e}")
        return False


def test_update_rule(rule_id: str):
    """Test updating a rule"""
    print_test(f"Update Rule: {rule_id}")

    try:
        payload = {
            "description": "Updated test description",
            "enabled": False
        }

        response = requests.put(f"{BASE_URL}/tag-rules/{rule_id}", json=payload)
        data = response.json()

        if response.status_code == 200 and data['success']:
            enabled = data['rule']['enabled']
            print_result(True, f"Updated rule, enabled: {enabled}")
            return True
        else:
            print_result(False, f"Failed to update rule: {data}")
            return False

    except Exception as e:
        print_result(False, f"Error: {e}")
        return False


def test_delete_rule(rule_id: str):
    """Test deleting a rule"""
    print_test(f"Delete Rule: {rule_id}")

    try:
        response = requests.delete(f"{BASE_URL}/tag-rules/{rule_id}")
        data = response.json()

        if response.status_code == 200 and data['success']:
            print_result(True, "Rule deleted successfully")
            return True
        else:
            print_result(False, f"Failed to delete rule: {data}")
            return False

    except Exception as e:
        print_result(False, f"Error: {e}")
        return False


def main():
    """Run all tests"""
    print("\n" + "="*60)
    print("AssetAtlas Backend API Test Suite")
    print("Tag Rules Endpoints")
    print("="*60)
    print("\nTesting API at:", BASE_URL)

    # Track results
    tests_passed = 0
    tests_failed = 0

    # Test 1: Health check
    if test_health_check():
        tests_passed += 1
    else:
        tests_failed += 1
        print("\n❌ API is not healthy. Make sure the server is running.")
        print("   Run: python tag_rules/api/app.py")
        sys.exit(1)

    # Test 2: Get fields
    if test_get_fields():
        tests_passed += 1
    else:
        tests_failed += 1

    # Test 3: Preview classification
    if test_preview_classification():
        tests_passed += 1
    else:
        tests_failed += 1

    # Test 4: Preview compliance
    if test_preview_compliance():
        tests_passed += 1
    else:
        tests_failed += 1

    # Test 5: Create rule
    rule_id = test_create_classification_rule()
    if rule_id:
        tests_passed += 1

        # Test 6: List rules
        if test_list_rules():
            tests_passed += 1
        else:
            tests_failed += 1

        # Test 7: Get rule
        if test_get_rule(rule_id):
            tests_passed += 1
        else:
            tests_failed += 1

        # Test 8: Update rule
        if test_update_rule(rule_id):
            tests_passed += 1
        else:
            tests_failed += 1

        # Test 9: Delete rule (cleanup)
        if test_delete_rule(rule_id):
            tests_passed += 1
        else:
            tests_failed += 1
    else:
        tests_failed += 1

    # Summary
    print("\n" + "="*60)
    print("TEST SUMMARY")
    print("="*60)
    print(f"Passed: {tests_passed}")
    print(f"Failed: {tests_failed}")
    print(f"Total:  {tests_passed + tests_failed}")

    if tests_failed == 0:
        print("\n✅ All tests passed!")
        sys.exit(0)
    else:
        print(f"\n❌ {tests_failed} test(s) failed")
        sys.exit(1)


if __name__ == "__main__":
    main()
