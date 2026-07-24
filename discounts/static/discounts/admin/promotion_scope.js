(function () {
    'use strict';

    const relationByScope = {
        breeds: 'breeds',
        specific_dogs: 'dogs',
    };

    function updateScopeFields() {
        const scopeField = document.getElementById('id_scope');
        if (!scopeField) {
            return;
        }

        const activeRelation = relationByScope[scopeField.value] || null;
        document
            .querySelectorAll('[data-promotion-scope-field]')
            .forEach((field) => {
                const row = field.closest('.form-row');
                if (row) {
                    row.hidden = field.dataset.promotionScopeField !== activeRelation;
                }
            });
    }

    document.addEventListener('DOMContentLoaded', () => {
        const scopeField = document.getElementById('id_scope');
        if (!scopeField) {
            return;
        }
        scopeField.addEventListener('change', updateScopeFields);
        updateScopeFields();
    });
})();
