/* The built-in library page (view:library, Plugin API 19): libraryPage() is the component its markup
   uses. Everything is MangaVibe.library; this only gives its names to the markup (items, bulk(),
   selCount... which plugins' markup in the page's slots uses too). Nothing else from the app but
   window.MangaVibe. */
MangaVibe.onStart(() => {
  Alpine.data("libraryPage", () => MangaVibe.bind(MangaVibe.library));
});
