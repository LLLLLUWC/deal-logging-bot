"""Test multi-deal message detection and handling."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from bot.utils.link_detector import LinkDetector


def test_multi_deck_detection():
    """Test detection of multiple deck links in a message."""
    detector = LinkDetector()

    # Test case 1: Multiple DocSend links (pitch + memo)
    text1 = """
    a friend of mine is raising for this proj

    Deck: https://docsend.com/view/2nuxmvuhu7cz7x2k
    Memo: https://docsend.com/view/vz8awwriap5frp9n
    """

    all_decks = detector.get_all_deck_links(text1)
    print(f"Test 1 - Multiple DocSend links:")
    print(f"  Found {len(all_decks)} deck link(s)")
    for deck in all_decks:
        print(f"    - {deck.link_type.value}: {deck.url}")
    assert len(all_decks) == 2, f"Expected 2 deck links, got {len(all_decks)}"
    assert detector.has_multiple_decks(text1), "Should detect multiple decks"

    # Test case 2: DocSend + calendar link (only 1 deck)
    text2 = """
    Check out our pitch deck: https://docsend.com/view/vjhru66tfzmrimbw
    Book a call: https://cal.com/aliposky/canopy-pitch
    """

    all_decks = detector.get_all_deck_links(text2)
    print(f"\nTest 2 - DocSend + calendar link:")
    print(f"  Found {len(all_decks)} deck link(s)")
    for deck in all_decks:
        print(f"    - {deck.link_type.value}: {deck.url}")
    assert len(all_decks) == 1, f"Expected 1 deck link, got {len(all_decks)}"
    assert not detector.has_multiple_decks(text2), "Should not detect multiple decks"

    # Test case 3: Mixed links (DocSend + Twitter + website)
    text3 = """
    Fluent is building...

    Twitter: https://x.com/fluentxyz
    Testnet: https://testnet.fluent.xyz/
    Deck: https://docsend.com/v/n7k8x/fluent_ecosystem_round
    """

    all_decks = detector.get_all_deck_links(text3)
    non_decks = detector.get_non_deck_links(text3)
    print(f"\nTest 3 - Mixed links:")
    print(f"  Found {len(all_decks)} deck link(s), {len(non_decks)} non-deck link(s)")
    for deck in all_decks:
        print(f"    Deck: {deck.link_type.value}: {deck.url}")
    for link in non_decks:
        print(f"    Other: {link.link_type.value}: {link.url}")
    assert len(all_decks) == 1, f"Expected 1 deck link, got {len(all_decks)}"

    # Test case 4: Loom + DocSend (both are deck-like)
    text4 = """
    Demo video: https://www.loom.com/share/c3095d9c6b1742a186584696a99530c4
    Pitch deck: https://docsend.com/view/example
    """

    all_decks = detector.get_all_deck_links(text4)
    print(f"\nTest 4 - Loom + DocSend:")
    print(f"  Found {len(all_decks)} deck link(s)")
    for deck in all_decks:
        print(f"    - {deck.link_type.value}: {deck.url} (priority: {deck.priority})")
    assert len(all_decks) == 2, f"Expected 2 deck links, got {len(all_decks)}"

    # Verify DocSend has higher priority than Loom
    best = detector.get_best_deck_link(text4)
    print(f"  Best deck link: {best.link_type.value}")
    assert best.link_type.value == "docsend", "DocSend should have higher priority than Loom"

    print("\n✅ All tests passed!")


def test_numbered_list_detection():
    """Test detection of numbered list format (multiple deals)."""
    # This would need LLM to detect, but we can test the text pattern
    text_multi_deal = """
    1. Optimum | X | Deck
    Building the fastest decentralized internet protocol
    https://docsend.com/view/optimum

    2. Stableport | Stablecoin payments
    B2B payments platform
    https://docsend.com/view/stableport

    3. Canopy | L1 infrastructure
    Launch production-ready L1s
    https://docsend.com/view/canopy
    """

    detector = LinkDetector()
    all_decks = detector.get_all_deck_links(text_multi_deal)
    print(f"\nTest - Numbered list format:")
    print(f"  Found {len(all_decks)} deck link(s)")
    for deck in all_decks:
        print(f"    - {deck.url}")

    # All 3 DocSend links should be detected
    assert len(all_decks) == 3, f"Expected 3 deck links, got {len(all_decks)}"

    print("\n✅ Numbered list test passed!")


if __name__ == "__main__":
    print("=" * 50)
    print("MULTI-DEAL MESSAGE TESTS")
    print("=" * 50)
    test_multi_deck_detection()
    test_numbered_list_detection()
