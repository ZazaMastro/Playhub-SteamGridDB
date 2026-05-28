import { FC, ImgHTMLAttributes } from 'react';
interface LazyImage extends ImgHTMLAttributes<HTMLImageElement | HTMLVideoElement> {
    isVideo?: boolean;
    unloadWhenOutside?: boolean;
    marginOffset?: IntersectionObserverInit['rootMargin'];
    scrollContainer?: IntersectionObserverInit['root'];
    src: string;
    wrapperProps?: any;
    blurBackground?: boolean;
}
export declare const LazyImage: FC<LazyImage>;
export default LazyImage;
