"""Language metadata must take precedence over a translated topic title."""
import unittest

from modules.organizer import Organizer


class OrganizerLanguageTests(unittest.TestCase):
    def test_russian_feeds_in_german_topic_stay_in_russian_playlist(self):
        for metadata in ('tvg-language="Russian"', 'tvg-id="news.ru"',
                         'tvg-id="news.ru@HD"'):
            for name in ('Новости', 'News Feed'):
                with self.subTest(metadata=metadata, name=name):
                    stream = {'info': f'#EXTINF:-1 {metadata} group-title="Nachrichten",{name}',
                              'url': 'http://example.invalid/stream', 'extra': []}
                    result = Organizer(llm=None).organize([stream])
                    self.assertIn(stream['url'] + '\n', result['ru_lines'])
                    self.assertNotIn(stream['url'] + '\n', result['foreign_lines'])

    def test_german_names_and_unmarked_german_topics_stay_foreign(self):
        for name, attrs in (('ZDF HD', {'tvg-language': 'Russian'}),
                            ('News Feed', {}), ('Nachrichten', {'tvg-id': 'news.ru'})):
            with self.subTest(name=name, attrs=attrs):
                score = Organizer._lang_score({'name': name, 'group': 'Nachrichten', 'attrs': attrs})
                self.assertLessEqual(score, -2)


if __name__ == '__main__':
    unittest.main()
