document.addEventListener('DOMContentLoaded', () => {
    const fetchOptions = {
        'headers': {
            'X-Load-More': true
        }
    }
    
    for (const btnLoadMore of document.getElementsByClassName('paginated-loader-button')) {
        const target = document.getElementById(btnLoadMore.dataset.target);

        btnLoadMore.addEventListener('click', async () => {
            let url = `${btnLoadMore.dataset.url}?page=${btnLoadMore.dataset.nextPage}`;
            
            if (btnLoadMore.dataset.filters) {
                url += `&${btnLoadMore.dataset.filters}`
            }

            const response = await fetch(url, fetchOptions);

            if (! response.ok) {
                console.error('failed to load more');
                btnLoadMore.closest('.paginated-loader-container').remove();
            }

            target.innerHTML += await response.text();

            if (++btnLoadMore.dataset.nextPage > btnLoadMore.dataset.totalPages) {
                btnLoadMore.closest('.paginated-loader-container').remove();
            }
        });
    }
});
