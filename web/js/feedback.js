function getFeedbackElements() {
    return {
        modal: document.getElementById('feedback-modal'),
        input: document.getElementById('feedback-message-input'),
        submitBtn: document.getElementById('feedback-submit-btn'),
        errorEl: document.getElementById('feedback-error-message'),
        countEl: document.getElementById('feedback-char-count'),
        formPanel: document.getElementById('feedback-form-panel'),
        successPanel: document.getElementById('feedback-success-panel'),
    };
}

function t(key, fallback) {
    return window.I18n?.t?.(key) || fallback;
}

function updateFeedbackCount() {
    const els = getFeedbackElements();
    if (!els.input || !els.countEl) return;
    els.countEl.textContent = `${els.input.value.length} / 2000`;
}

function setFeedbackSubmitting(submitting) {
    const { submitBtn } = getFeedbackElements();
    if (!submitBtn) return;
    submitBtn.disabled = submitting;
    submitBtn.classList.toggle('opacity-60', submitting);
    submitBtn.classList.toggle('cursor-not-allowed', submitting);
}

function showFeedbackError(message) {
    const { errorEl } = getFeedbackElements();
    if (!errorEl) return;
    if (message) {
        errorEl.textContent = message;
        errorEl.classList.remove('hidden');
    } else {
        errorEl.textContent = '';
        errorEl.classList.add('hidden');
    }
}

function setFeedbackView(view) {
    const { formPanel, successPanel } = getFeedbackElements();
    if (!formPanel || !successPanel) return;

    const showSuccess = view === 'success';
    formPanel.classList.toggle('hidden', showSuccess);
    successPanel.classList.toggle('hidden', !showSuccess);
    window.AppUtils?.refreshIcons?.();
}

window.openFeedbackModal = function () {
    const { modal, input } = getFeedbackElements();
    if (!modal) return;
    modal.classList.remove('hidden');
    modal.classList.add('flex');
    setFeedbackView('form');
    showFeedbackError('');
    updateFeedbackCount();
    window.AppUtils?.refreshIcons?.();
    setTimeout(() => input?.focus(), 0);
};

window.closeFeedbackModal = function () {
    const { modal, input } = getFeedbackElements();
    if (!modal) return;
    modal.classList.add('hidden');
    modal.classList.remove('flex');
    if (input) {
        input.value = '';
    }
    setFeedbackView('form');
    showFeedbackError('');
    updateFeedbackCount();
};

window.submitFeedbackMessage = async function () {
    const { input } = getFeedbackElements();
    if (!input) return;

    const message = (input.value || '').trim();
    if (!message) {
        showFeedbackError(t('settings.feedback.validationEmpty', 'Please enter your feedback before submitting.'));
        return;
    }

    const language = window.I18n?.getLanguage?.() || 'zh-TW';

    try {
        setFeedbackSubmitting(true);
        showFeedbackError('');
        await AppAPI.post('/api/user-feedback', { message, language });
        input.value = '';
        updateFeedbackCount();
        setFeedbackView('success');
        if (typeof showToast === 'function') {
            showToast(t('settings.feedback.submitSuccess', 'Feedback submitted successfully.'), 'success');
        }
    } catch (error) {
        showFeedbackError(t('settings.feedback.submitFailed', 'Failed to submit feedback.'));
    } finally {
        setFeedbackSubmitting(false);
    }
};

document.addEventListener('DOMContentLoaded', () => {
    const { input, modal } = getFeedbackElements();
    if (input) {
        input.addEventListener('input', () => {
            updateFeedbackCount();
            if ((input.value || '').trim()) {
                showFeedbackError('');
            }
        });
    }
    if (modal) {
        modal.addEventListener('keydown', (event) => {
            if (event.key === 'Escape') {
                window.closeFeedbackModal();
            }
        });
    }
});
