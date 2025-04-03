from django.shortcuts import render, redirect
from django.core.paginator import Paginator

from fortissimusbellator.parsers import to_int
from blog.models import Post


def post_list(request):
    posts = Post.posts_published.all()

    # Pagination
    page = to_int(request.GET.get('page'), or_default=1)
    per_page = to_int(request.GET.get('per_page'), or_default=12)

    if per_page <= 0:
        per_page = 1

    paginator = Paginator(posts, per_page)
    paginated_posts = paginator.get_page(page)

    context = {
        'posts': paginated_posts,
        'pagination': {
            'has_more': paginated_posts.has_next(),  # Show "Load More" if there are more pages
            'next_page': paginated_posts.next_page_number() if paginated_posts.has_next() else None,
            'total_pages': paginator.num_pages,
        }
    }

    if request.headers.get('X-Load-More'):
        return render(request, 'blog/partials/cards.html', context)

    return render(request, 'blog/index.html', context)


def post_detail(request, post_id: int):
    queryset = Post.posts_published

    try:
        post = queryset.get(pk=post_id)

        related_posts = queryset.filter(
            categories__in=post.categories.all()
        ).exclude(
            pk=post.pk
        ).distinct()[:4]

        context = {
            'post': post,
            'related_posts': related_posts,
        }

        return render(request, 'blog/detail.html', context)
    except Post.DoesNotExist:
        return redirect('blog:posts')
