import { FC, CSSProperties } from 'react';
export declare enum FooterGlyphType {
    Knockout = 0,
    Light = 1,
    Dark = 2
}
export declare enum FooterGlyphSize {
    Small = 0,
    Medium = 1,
    Large = 2
}
declare const FooterGlyph: FC<{
    button: number;
    type?: FooterGlyphType;
    size?: FooterGlyphSize;
    style?: CSSProperties;
}>;
export default FooterGlyph;
