import { FC } from 'react';
export interface MotdApiResponse {
    id: string;
    url: string;
    text: string | null;
    text_sub: string | null;
    image_url: string;
    bg_type: 'image' | 'video';
    bg_url: string;
    bg_color: string;
    bg_position: 'center center' | 'left top' | 'left center' | 'left bottom' | 'right top' | 'right center' | 'right bottom' | 'center top' | 'center center' | 'center bottom';
    expires: number;
}
export interface Motd {
    disabled: boolean;
    noFocusRing?: boolean;
}
declare const Motd: FC<Motd>;
export default Motd;
