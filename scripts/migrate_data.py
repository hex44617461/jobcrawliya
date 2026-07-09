"""Move existing scraped posts and images into new data/scraped structure.

This script moves files from `jobcrawliya/post` -> `data/scraped/posts`
and `jobcrawliya/img` -> `data/scraped/images` using shutil.move so
binary images are preserved.
"""
import os
import shutil


def ensure_dir(path):
    os.makedirs(path, exist_ok=True)


def move_tree(src_dir, dst_dir):
    if not os.path.exists(src_dir):
        print(f"[skip] source not found: {src_dir}")
        return 0
    ensure_dir(dst_dir)
    moved = 0
    for name in os.listdir(src_dir):
        src = os.path.join(src_dir, name)
        dst = os.path.join(dst_dir, name)
        try:
            shutil.move(src, dst)
            moved += 1
            print(f"moved: {src} -> {dst}")
        except Exception as e:
            print(f"error moving {src}: {e}")
    return moved


def main():
    base = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    src_base = os.path.join(base, "jobcrawliya")
    src_posts = os.path.join(src_base, "post")
    src_imgs = os.path.join(src_base, "img")

    dst_base = os.path.join(base, "data", "scraped")
    dst_posts = os.path.join(dst_base, "posts")
    dst_imgs = os.path.join(dst_base, "images")

    print(f"src_posts: {src_posts}\nsrc_imgs: {src_imgs}\ndst_posts: {dst_posts}\ndst_imgs: {dst_imgs}")

    moved_posts = move_tree(src_posts, dst_posts)
    moved_imgs = move_tree(src_imgs, dst_imgs)

    print(f"Done. moved_posts={moved_posts}, moved_imgs={moved_imgs}")


if __name__ == "__main__":
    main()
