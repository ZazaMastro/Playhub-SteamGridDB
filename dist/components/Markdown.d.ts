import { FocusableProps } from '@decky/ui';
import { FC } from 'react';
import { Options } from 'react-markdown';
declare const Markdown: FC<{
    onLinkClick?: () => void;
    focusableProps?: Partial<FocusableProps>;
} & Options>;
export default Markdown;
