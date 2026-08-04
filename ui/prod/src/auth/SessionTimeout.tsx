// SPDX-License-Identifier: AGPL-3.0-or-later
import { useState, useEffect, useCallback, useRef } from 'react';
import { Modal } from '../components/ui/Modal';
import { Button } from '../components/ui/Button';
import { useAuth } from './useAuth';
import { config, refreshAccessToken, getLastActivity } from './auth-service';
import styles from './SessionTimeout.module.css';

const WARNING_SECONDS = 120;
const WARNING_BEFORE_EXPIRY = WARNING_SECONDS * 1000;

export function SessionTimeout() {
  const { logout } = useAuth();
  const [showModal, setShowModal] = useState(false);
  const [countdown, setCountdown] = useState(WARNING_SECONDS);
  const warningTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const countdownTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const clearTimers = useCallback(() => {
    if (warningTimerRef.current !== null) {
      clearTimeout(warningTimerRef.current);
      warningTimerRef.current = null;
    }
    if (countdownTimerRef.current !== null) {
      clearInterval(countdownTimerRef.current);
      countdownTimerRef.current = null;
    }
  }, []);

  const startWarningTimer = useCallback(() => {
    clearTimers();

    const lastAct = getLastActivity();
    const elapsed = Date.now() - lastAct;
    const warningDelay = config.sessionTimeout - WARNING_BEFORE_EXPIRY - elapsed;

    if (warningDelay <= 0) {
      setShowModal(true);
      setCountdown(WARNING_SECONDS);
      startCountdown();
      return;
    }

    warningTimerRef.current = setTimeout(() => {
      setShowModal(true);
      setCountdown(WARNING_SECONDS);
      startCountdown();
    }, warningDelay);
  }, [clearTimers]);

  const startCountdown = useCallback(() => {
    if (countdownTimerRef.current !== null) {
      clearInterval(countdownTimerRef.current);
    }

    let remaining = WARNING_SECONDS;
    countdownTimerRef.current = setInterval(() => {
      remaining -= 1;
      setCountdown(remaining);

      if (remaining <= 0) {
        if (countdownTimerRef.current !== null) {
          clearInterval(countdownTimerRef.current);
          countdownTimerRef.current = null;
        }
        logout();
      }
    }, 1000);
  }, [logout]);

  const handleStayLoggedIn = useCallback(async () => {
    clearTimers();
    setShowModal(false);

    try {
      await refreshAccessToken();
    } catch {
      logout();
      return;
    }

    startWarningTimer();
  }, [clearTimers, startWarningTimer, logout]);

  const handleLogout = useCallback(() => {
    clearTimers();
    setShowModal(false);
    logout();
  }, [clearTimers, logout]);

  useEffect(() => {
    startWarningTimer();

    const events = ['mousedown', 'keydown', 'scroll', 'touchstart', 'click'];
    const handleActivity = () => {
      if (!showModal) {
        startWarningTimer();
      }
    };

    events.forEach((event) => {
      window.addEventListener(event, handleActivity, { passive: true });
    });

    return () => {
      clearTimers();
      events.forEach((event) => {
        window.removeEventListener(event, handleActivity);
      });
    };
  }, [startWarningTimer, clearTimers, showModal]);

  useEffect(() => {
    if (!showModal) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Enter') {
        e.preventDefault();
        handleStayLoggedIn();
      }
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [showModal, handleStayLoggedIn]);

  const minutes = Math.floor(countdown / 60);
  const seconds = countdown % 60;
  const timeDisplay = `${minutes}:${seconds.toString().padStart(2, '0')}`;

  return (
    <Modal
      isOpen={showModal}
      onClose={handleLogout}
      title="Session Expiring"
      size="sm"
      closeOnEscape={false}
      closeOnBackdrop={false}
      showCloseButton={false}
    >
      <p className={styles.message}>
        Your session will expire in{' '}
        <strong className={styles.countdown}>{timeDisplay}</strong> due to
        inactivity.
      </p>
      <p className={`${styles.message} ${styles.messageLast}`}>
        Would you like to stay logged in?
      </p>
      <div className={styles.footer}>
        <Button variant="secondary" onClick={handleLogout}>
          Log Out
        </Button>
        <Button variant="primary" onClick={handleStayLoggedIn}>
          Stay Logged In
        </Button>
      </div>
    </Modal>
  );
}
