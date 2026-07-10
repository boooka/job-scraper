'use strict';
// Turns the changelist filter <select class="admin-autocomplete-filter"> into a
// Select2 box that queries the admin autocomplete endpoint after 2 typed chars
// and reloads the changelist with the chosen city/company id as a query param.
{
    const $ = django.jQuery;

    $(function () {
        $('select.admin-autocomplete-filter').each(function () {
            const el = $(this);
            el.select2({
                ajax: {
                    url: el.data('ajax--url'),
                    dataType: 'json',
                    delay: 200,
                    data: function (params) {
                        return {
                            term: params.term || '',
                            app_label: el.data('app-label'),
                            model_name: el.data('model-name'),
                            field_name: el.data('field-name'),
                        };
                    },
                    processResults: function (data) {
                        return { results: data.results };
                    },
                },
                minimumInputLength: 2,
                allowClear: true,
                placeholder: el.data('placeholder') || '',
                width: '95%',
            });

            // Filters live outside a form — navigate to the filtered URL on change.
            el.on('change', function () {
                const params = new URLSearchParams(window.location.search);
                const name = el.attr('name');
                const val = el.val();
                if (val) {
                    params.set(name, val);
                } else {
                    params.delete(name);
                }
                params.delete('p'); // reset pagination
                window.location.search = params.toString();
            });
        });
    });
}
