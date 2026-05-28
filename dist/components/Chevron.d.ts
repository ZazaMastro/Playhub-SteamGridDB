import { FC, SVGProps } from 'react';
interface ChevronProps extends SVGProps<SVGSVGElement> {
    direction: 'up' | 'down' | 'left' | 'right';
}
declare const Chevron: FC<ChevronProps>;
export default Chevron;
