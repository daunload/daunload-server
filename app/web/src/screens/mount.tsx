/* #view에 Preact 화면을 그린다.
 * 아직 옮기지 않은 화면은 #view.innerHTML을 통째로 바꾸므로, Preact 화면은 #view 안의 전용 컨테이너에 그린다.
 * 같은 화면을 다시 그리면(5초 폴링 등) 같은 컨테이너에 그려 포커스·펼침·입력 상태를 지킨다.
 * 다른 화면으로 바꾸기 전에 app.js의 renderView()가 unmountScreen()을 불러 컴포넌트를 정리한다. */
import { type ComponentChild, render } from "preact";

let mounted: HTMLElement | null = null;

export function mountScreen(name: string, node: ComponentChild): void {
  const view = document.getElementById("view")!;
  if (!mounted || mounted.dataset.screen !== name || mounted.parentElement !== view) {
    unmountScreen();
    mounted = document.createElement("div");
    mounted.dataset.screen = name;
    mounted.className = "contents";  // 카드들이 #view(grid)의 항목이 되도록 상자를 만들지 않는다
    view.replaceChildren(mounted);
  }
  render(node, mounted);
}

export function unmountScreen(): void {
  if (mounted) render(null, mounted);
  mounted = null;
}
