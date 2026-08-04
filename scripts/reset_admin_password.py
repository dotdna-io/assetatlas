#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Reset (and activate) the admin user's password.

Usage:
    DATABASE_URL="postgresql://..." python scripts/reset_admin_password.py <new_password>

The admin user is created in a locked state by migration 025. This script sets
its password and activates it (is_active = true). A password argument is
required — there is no default.
"""

import sys
import os
import bcrypt
import psycopg2


def reset_admin_password(new_password):
    """Reset the admin user's password and activate the account."""

    db_url = os.environ.get('DATABASE_URL')
    if not db_url:
        print("❌ DATABASE_URL environment variable not set")
        print("   Usage: DATABASE_URL='postgresql://...' python scripts/reset_admin_password.py <new_password>")
        sys.exit(1)

    try:
        # Connect to database
        conn = psycopg2.connect(db_url)
        cursor = conn.cursor()

        # Hash the new password
        password_hash = bcrypt.hashpw(new_password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

        # Update admin password and activate the account
        cursor.execute(
            """
            UPDATE users
            SET password_hash = %s,
                is_active = true,
                updated_at = NOW()
            WHERE username = 'admin'
            RETURNING id, username, role
            """,
            (password_hash,)
        )

        result = cursor.fetchone()

        if result:
            conn.commit()
            print(f"✅ Password reset successful (admin activated)!")
            print(f"   Username: {result[1]}")
            print(f"   Role: {result[2]}")
            print(f"\n   You can now log in at: http://localhost:3000/login")
        else:
            print("❌ Admin user not found in database")
            sys.exit(1)

        cursor.close()
        conn.close()

    except Exception as e:
        print(f"❌ Error resetting password: {e}")
        raise


if __name__ == '__main__':
    if len(sys.argv) < 2 or not sys.argv[1]:
        print("Usage: python scripts/reset_admin_password.py <new_password>")
        sys.exit(1)
    reset_admin_password(sys.argv[1])
