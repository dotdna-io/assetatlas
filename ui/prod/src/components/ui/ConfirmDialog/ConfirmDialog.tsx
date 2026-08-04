// SPDX-License-Identifier: AGPL-3.0-or-later
import { Modal } from '../Modal';
import { Button } from '../Button';
import styles from './ConfirmDialog.module.css';

type ConfirmDialogVariant = 'danger' | 'warning';

interface ConfirmDialogProps {
  isOpen: boolean;
  onConfirm: () => void;
  onCancel: () => void;
  title: string;
  message: string;
  confirmLabel?: string;
  cancelLabel?: string;
  variant?: ConfirmDialogVariant;
}

export function ConfirmDialog({
  isOpen,
  onConfirm,
  onCancel,
  title,
  message,
  confirmLabel = 'Confirm',
  cancelLabel = 'Cancel',
  variant = 'danger',
}: ConfirmDialogProps) {
  // Button only has 'danger'; map 'warning' to 'primary' (less destructive styling)
  const buttonVariant = variant === 'danger' ? 'danger' : 'primary';

  return (
    <Modal
      isOpen={isOpen}
      onClose={onCancel}
      title={title}
      size="sm"
      showCloseButton={false}
      titleAlign="center"
    >
      <p className={styles.message}>{message}</p>
      <div className={styles.footer}>
        <Button variant="secondary" onClick={onCancel}>
          {cancelLabel}
        </Button>
        <Button variant={buttonVariant} onClick={onConfirm}>
          {confirmLabel}
        </Button>
      </div>
    </Modal>
  );
}
