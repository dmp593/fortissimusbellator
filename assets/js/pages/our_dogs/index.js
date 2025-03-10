document.addEventListener('DOMContentLoaded', () => {
    const btnLoadMore = document.getElementById('load-more');
    const dogsGrid = document.getElementById('dogs-grid');

    btnLoadMore?.addEventListener('click', async () => {
        const response = await fetch(
            `${btnLoadMore.dataset.url}?page=${btnLoadMore.dataset.nextPage}&${btnLoadMore.dataset.filters}`,
            {
                'headers': { 'X-Load-More': true }
            }
        );

        if (! response.ok) {
            console.error('Failed to load more dogs');
            document.getElementById('dogs-load-more').remove();
        }

        dogsGrid.innerHTML += await response.text();

        if (++btnLoadMore.dataset.nextPage > btnLoadMore.dataset.totalPages) {
            document.getElementById('dogs-load-more').remove();
        }
    });
});
