from pitwall.engineer.speech import Speaker, int_words_de, int_words_en


def test_german_numbers():
    assert int_words_de(0) == "null"
    assert int_words_de(1) == "eins"
    assert int_words_de(21) == "einundzwanzig"
    assert int_words_de(32) == "zweiunddreißig"
    assert int_words_de(101) == "hunderteins"
    assert int_words_de(1999) == "tausendneunhundertneunundneunzig"
    assert int_words_en(32) == "thirty-two"


def test_radio_formats_de():
    sp = Speaker("de")
    assert sp.decimal(0.4).spoken == "null Komma vier"
    assert sp.decimal(0.4).display == "0,4"
    assert sp.laptime(92.53).spoken == "eins zweiunddreißig Komma fünf"
    assert sp.laptime(92.53).display == "1:32,5"
    assert sp.laptime(65.04).spoken == "eins null fünf Komma null"
    assert sp.position(3).spoken == "P drei"
    assert sp.position(3).display == "P3"
    assert sp.litres(2.3).spoken == "zwei Komma drei Liter"
    assert sp.n_laps(1, "dat").spoken == "einer Runde"
    assert sp.n_laps(3).spoken == "drei Runden"
    assert sp.seconds(2.0).display == "2,0"


def test_radio_formats_en():
    sp = Speaker("en")
    assert sp.decimal(0.4).spoken == "point four"
    assert sp.laptime(92.5).spoken == "one thirty-two point five"
    assert sp.position(12).spoken == "P twelve"
