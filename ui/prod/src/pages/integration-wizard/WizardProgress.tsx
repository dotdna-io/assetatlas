// SPDX-License-Identifier: AGPL-3.0-or-later
import type { WizardStep, WizardState, WizardMode } from './wizard-state';
import { STEP_LABELS } from './wizard-state';
import styles from './WizardProgress.module.css';

interface WizardProgressProps {
  currentStep: WizardStep;
  integrationType: string | null;
  validation: WizardState['validation'];
  mode: WizardMode;
  onStepClick: (step: WizardStep) => void;
}

export function WizardProgress({ currentStep, integrationType, mode, onStepClick }: WizardProgressProps) {
  if (!integrationType) return null;

  const steps = [1, 2, 3, 4] as const;
  const currentStepNum = typeof currentStep === 'number' ? currentStep : 0;

  return (
    <div className={styles.progress}>
      {steps.map((step, index) => {
        const isCompleted = step < currentStepNum;
        const isActive = step === currentStepNum;

        const isClickable = isCompleted || mode === 'edit';

        const circleClass = [
          styles.stepCircle,
          isActive ? styles.stepCircleActive : '',
          isCompleted ? styles.stepCircleCompleted : '',
          isClickable && !isActive ? styles.stepCircleClickable : '',
        ]
          .filter(Boolean)
          .join(' ');

        const labelClass = [styles.stepLabel, isActive ? styles.stepLabelActive : '']
          .filter(Boolean)
          .join(' ');

        return (
          <div key={step} style={{ display: 'contents' }}>
            {index > 0 && (
              <div className={[styles.line, isCompleted ? styles.lineCompleted : ''].filter(Boolean).join(' ')} />
            )}
            <div className={styles.step}>
              <div
                className={circleClass}
                onClick={isClickable ? () => onStepClick(step as WizardStep) : undefined}
                role={isClickable ? 'button' : undefined}
                tabIndex={isClickable ? 0 : undefined}
                onKeyDown={
                  isClickable
                    ? (e) => { if (e.key === 'Enter' || e.key === ' ') onStepClick(step as WizardStep); }
                    : undefined
                }
              >
                {isCompleted ? <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M20 6L9 17l-5-5"/></svg> : step}
              </div>
              <span className={labelClass}>{STEP_LABELS[step]}</span>
            </div>
          </div>
        );
      })}
    </div>
  );
}
